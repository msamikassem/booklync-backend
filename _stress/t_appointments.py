"""Appointments: happy path, the full 409 validation matrix, batch atomicity,
status transitions (Sale hook), reschedule rules, RBAC, tenant isolation."""
import datetime
from core import call, body, ok, reject, finding

A = "appointments"

def _mk_emp(F, tA, name, hours="09:00:00-17:00:00", capable=(None,), bookable=True):
    """Create a disposable employee at A1 with working hours + capabilities."""
    A1 = F["A1"]
    e, good = ok(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [A1], "name": name, "can_receive_bookings": bookable}), A, f"setup emp {name}")
    if not good:
        return None
    eid = e["id"]
    if bookable:
        st, en = hours.split("-")
        wh = [{"day_of_week": d, "start_time": st, "end_time": en} for d in range(7)]
        call("POST", f"/employees/{eid}/working-hours", token=tA, params={"agent_id": A1},
             json_body={"working_hours": wh})
        for sid in capable:
            if sid:
                call("POST", f"/employees/{eid}/capabilities", token=tA, params={"agent_id": A1},
                     json_body={"service_id": sid})
    return eid

def run(F):
    tA, tB, tR, tS = F["A_token"], F["B_token"], F["recep_token"], F["staff_token"]
    A1, A2, B1 = F["A1"], F["A2"], F["B1"]
    C1, E1, E2 = F["C1"], F["E1"], F["E2"]
    S1, S2, S3 = F["S1"], F["S2"], F["S3"]
    d0 = F["future_date"]
    dt10, dt11, dt14 = F["future_dt_10"], F["future_dt_11"], F["future_dt_14"]
    wd = datetime.date.fromisoformat(d0).weekday()

    def at(hhmm):  # build UTC iso on d0
        return f"{d0}T{hhmm}:00+00:00"

    # ---- happy create ----
    apt1, good = ok(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": dt10, "source": "manual"}), A, "create S1@10:00 E1", codes=(200, 201))
    APT1 = apt1["id"] if good else None
    if good:
        st, en = apt1.get("start_time"), apt1.get("end_time")
        try:
            sdt = datetime.datetime.fromisoformat(st)
            edt = datetime.datetime.fromisoformat(en)
            if (edt - sdt) != datetime.timedelta(minutes=30):
                finding("HIGH", A, "end_time - start_time != service duration",
                        f"start={st} end={en} delta={edt - sdt}", "create")
            # documented contract: start_time is UTC (+00:00). Agent A1 is UTC.
            off = sdt.utcoffset()
            if off is not None and off != datetime.timedelta(0):
                finding("HIGH", A,
                        "start_time serialized in host-local offset, not UTC (contract & docs say UTC +00:00)",
                        f"start_time={st} (offset {off}) for a UTC agent. The instant is correct, but a client "
                        f"reading the date portion of start_time can land on the WRONG day near midnight. "
                        f"Caused by TIMESTAMPTZ rendering in the host session tz; depends on server tz.",
                        "tz-serialization")
        except Exception as e:
            finding("LOW", A, "could not parse appointment times", f"{st}/{en}: {e}", "create")
        for k in ("start_time_local", "service_color", "preparation_before_minutes"):
            if k not in apt1:
                finding("LOW", A, f"detail missing '{k}'", list(apt1), "create")

    # ---- SLOT_UNAVAILABLE: exact overlap on same employee ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S3,
        "start_time": dt10, "source": "manual"}), A, "double-book same slot (SLOT_UNAVAILABLE)",
        {409}, expect_code="SLOT_UNAVAILABLE", sev2xx="HIGH")

    # ---- PREP_TIME_CONFLICT: S1 prep_after=10 blocks until 10:40; book S3 @10:35 (no raw overlap) ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S3,
        "start_time": at("10:35"), "source": "manual"}), A, "prep-time gap (PREP_TIME_CONFLICT)",
        {409}, expect_code="PREP_TIME_CONFLICT", sev2xx="HIGH")

    # ---- PAST_APPOINTMENT ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": F["past_dt"], "source": "manual"}), A, "past appointment",
        {409}, expect_code="PAST_APPOINTMENT", sev2xx="HIGH")

    # ---- OUTSIDE business/working hours: book @18:00 (after 17:00 close) ----
    b, _ = reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": at("18:00"), "source": "manual"}), A, "after-hours booking",
        {409}, sev2xx="HIGH")
    if isinstance(b, dict) and b.get("code") not in ("OUTSIDE_BUSINESS_HOURS", "OUTSIDE_WORKING_HOURS"):
        finding("LOW", A, "after-hours code unexpected", f"code={b.get('code') if isinstance(b,dict) else b}", "after-hours")

    # ---- VARIATION_REQUIRED: book variation-service S2 without variation_id ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S2,
        "start_time": dt14, "source": "manual"}), A, "variation service w/o variation_id",
        {409, 400, 422}, expect_code="VARIATION_REQUIRED", sev2xx="HIGH")

    # ---- capability: E2 can't do S2 ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E2, "service_id": S2,
        "variation_id": F["S2_var_short"], "start_time": dt14, "source": "manual"}), A,
        "book service employee can't perform", {409, 400, 422}, sev2xx="HIGH")

    # ---- OUTSIDE_WORKING_HOURS via narrow-hours employee (09-10), book @14:00 ----
    eN = _mk_emp(F, tA, "Narrow Hours", hours="09:00:00-10:00:00", capable=(S1,))
    if eN:
        reject(call("POST", "/appointments/", token=tA, json_body={
            "agent_id": A1, "customer_id": C1, "employee_id": eN, "service_id": S1,
            "start_time": dt14, "source": "manual"}), A, "outside employee working hours",
            {409}, expect_code="OUTSIDE_WORKING_HOURS", sev2xx="HIGH")

    # ---- BREAK_CONFLICT via recurring break on d0 weekday 14:00-15:00 ----
    eBr = _mk_emp(F, tA, "Break Worker", capable=(S1,))
    if eBr:
        call("POST", f"/employees/{eBr}/blocked-times", token=tA, params={"agent_id": A1},
             json_body={"day_of_week": wd, "start_time": "14:00:00", "end_time": "15:00:00", "reason": "br"})
        reject(call("POST", "/appointments/", token=tA, json_body={
            "agent_id": A1, "customer_id": C1, "employee_id": eBr, "service_id": S1,
            "start_time": dt14, "source": "manual"}), A, "break conflict",
            {409}, expect_code="BREAK_CONFLICT", sev2xx="HIGH")

    # ---- EMPLOYEE_DAY_OFF via personal day-off covering d0 ----
    eDo = _mk_emp(F, tA, "DayOff Worker", capable=(S1,))
    if eDo:
        call("POST", f"/employees/{eDo}/days-off", token=tA, params={"agent_id": A1},
             json_body={"name": "off", "start_date": d0, "end_date": d0})
        reject(call("POST", "/appointments/", token=tA, json_body={
            "agent_id": A1, "customer_id": C1, "employee_id": eDo, "service_id": S1,
            "start_time": dt11, "source": "manual"}), A, "employee day off",
            {409}, expect_code="EMPLOYEE_DAY_OFF", sev2xx="HIGH")

    # ---- EMPLOYEE_NOT_BOOKABLE: bookable emp w/ capability, then flip to not-bookable ----
    eNb = _mk_emp(F, tA, "ToDisable", capable=(S1,))
    if eNb:
        call("PUT", f"/employees/{eNb}", token=tA, params={"agent_id": A1},
             json_body={"can_receive_bookings": False})
        reject(call("POST", "/appointments/", token=tA, json_body={
            "agent_id": A1, "customer_id": C1, "employee_id": eNb, "service_id": S1,
            "start_time": dt11, "source": "manual"}), A, "book non-bookable employee",
            {409}, expect_code="EMPLOYEE_NOT_BOOKABLE", sev2xx="HIGH")

    # ---- BRANCH_DAY_OFF on a separate date so it doesn't disturb d0 ----
    d_off = (datetime.date.fromisoformat(d0) + datetime.timedelta(days=1)).isoformat()
    doff = body(call("POST", "/days-off/", token=tA, json_body={
        "agent_id": A1, "name": "closed", "start_date": d_off, "end_date": d_off}))
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": f"{d_off}T11:00:00+00:00", "source": "manual"}), A, "branch day off",
        {409}, expect_code="BRANCH_DAY_OFF", sev2xx="HIGH")
    if isinstance(doff, dict) and doff.get("id"):
        call("DELETE", f"/days-off/{doff['id']}", token=tA, params={"agent_id": A1})

    # ---- body validation ----
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": "not-a-time", "source": "manual"}), A, "bad start_time format", {422, 400})
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": dt11, "source": "not_a_source"}), A, "invalid source enum", {422, 400})
    reject(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": 99999999, "employee_id": E1, "service_id": S1,
        "start_time": dt11, "source": "manual"}), A, "unknown customer", {404, 400, 409})

    # ---- tenant isolation ----
    reject(call("POST", "/appointments/", token=tB, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": dt11, "source": "manual"}), A, "B books into A's branch", {403, 404}, sev2xx="CRITICAL")
    if APT1:
        reject(call("GET", f"/appointments/{APT1}", token=tB), A, "B reads A's appointment", {403, 404}, sev2xx="CRITICAL")

    # ---- list / get / filters ----
    ok(call("GET", "/appointments/", token=tA, params={"agent_id": A1}), A, "list appts")
    ok(call("GET", "/appointments/", token=tA, params={"agent_id": A1, "status": "pending",
       "date_from": f"{d0}T00:00:00+00:00", "date_to": f"{d0}T23:59:59+00:00"}), A, "list appts filtered")
    reject(call("GET", "/appointments/", token=tA), A, "list appts missing agent_id", {422})
    if APT1:
        ok(call("GET", f"/appointments/{APT1}", token=tA), A, "get appt")

    # ---- RBAC: staff has no branch-calendar permission here ----
    if "view_branch_calendar" not in F["staff_perms"]:
        reject(call("GET", "/appointments/", token=tS, params={"agent_id": A1}), A,
               "staff hits branch calendar", {403})

    # ---- reschedule (PUT) ----
    if APT1:
        ok(call("PUT", f"/appointments/{APT1}", token=tA, json_body={"start_time": at("12:00"), "note": "moved"}),
           A, "reschedule appt")
        # reschedule onto a slot occupied by nobody is fine; now move to overlap with itself? skip
    # ---- status transitions + Sale hook ----
    aptC, good = ok(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "employee_id": E1, "service_id": S1,
        "start_time": at("15:00"), "source": "manual"}), A, "create appt for completion", codes=(200, 201))
    if good:
        APTC = aptC["id"]
        ok(call("PATCH", f"/appointments/{APTC}/status", token=tA, json_body={"status": "confirmed"}), A, "status->confirmed")
        ok(call("PATCH", f"/appointments/{APTC}/status", token=tA, json_body={"status": "completed"}), A, "status->completed (Sale hook)")
        # verify a sale now exists for this branch
        sales = body(call("GET", "/financials/sales", token=tA, params={"agent_id": A1}))
        if isinstance(sales, dict):
            has = any(any(li.get("appointment_id") == APTC for li in s.get("line_items", []))
                      for s in sales.get("sales", []))
            if not has:
                finding("HIGH", A, "completing appointment did NOT create a Sale", f"appt={APTC}", "Sale hook")
        # move back out of completed -> should void the (unpaid) sale
        ok(call("PATCH", f"/appointments/{APTC}/status", token=tA, json_body={"status": "confirmed"}), A, "status back to confirmed")
        # PUT on a non-editable status
        ok(call("PATCH", f"/appointments/{APTC}/cancel", token=tA), A, "cancel appt (shortcut)")
        reject(call("PUT", f"/appointments/{APTC}", token=tA, json_body={"note": "x"}), A,
               "edit cancelled appt", {409, 400}, expect_code="APPOINTMENT_CLOSED", sev2xx="HIGH")
    reject(call("PATCH", "/appointments/99999999/status", token=tA, json_body={"status": "completed"}), A,
           "status on unknown appt", {404})
    reject(call("PATCH", f"/appointments/{APT1}/status", token=tA, json_body={"status": "banana"}), A,
           "invalid status enum", {422, 400})

    # ---- batch ----
    bt, good = ok(call("POST", "/appointments/batch", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "start_time": at("13:00"), "source": "manual",
        "items": [{"service_id": S1, "employee_id": E1}, {"service_id": S3, "employee_id": E1}]}), A,
        "batch 2 services back-to-back", codes=(200, 201))
    if good:
        appts = bt.get("appointments", [])
        gids = {a.get("booking_group_id") for a in appts}
        if len(appts) >= 2 and (len(gids) != 1 or None in gids):
            finding("HIGH", A, "batch lines don't share one booking_group_id", f"gids={gids}", "batch")
    # batch atomicity: one item unbookable (past) -> none created
    before = body(call("GET", "/appointments/", token=tA, params={"agent_id": A1}))
    n_before = before.get("total") if isinstance(before, dict) else None
    reject(call("POST", "/appointments/batch", token=tA, json_body={
        "agent_id": A1, "customer_id": C1, "start_time": F["past_dt"], "source": "manual",
        "items": [{"service_id": S1, "employee_id": E1}]}), A, "batch with unbookable item", {409, 400}, sev2xx="HIGH")
    after = body(call("GET", "/appointments/", token=tA, params={"agent_id": A1}))
    n_after = after.get("total") if isinstance(after, dict) else None
    if n_before is not None and n_after is not None and n_after != n_before:
        finding("HIGH", A, "batch not atomic: count changed after a failed batch",
                f"before={n_before} after={n_after}", "batch atomicity")
