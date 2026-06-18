"""Build the shared test world. Returns a dict F of ids/tokens."""
import datetime
from core import call, body, ok, finding, seed_user, RUN_TAG

PW = "Password123!"

def _login(email, password):
    r = call("POST", "/auth/login", json_body={"email": email, "password": password})
    b = body(r) or {}
    return b.get("access_token")

def build():
    F = {}
    P = RUN_TAG[-6:]                       # run-unique 6-digit phone base
    ph = lambda n: f"+1{P}{n:04d}"         # -> +1 + 10 digits, unique per run+entity
    # ---- Tenant A (owner) ----
    emailA = f"ownerA_{RUN_TAG}@qa-booklync.com"
    r = call("POST", "/auth/register", json_body={
        "name": "Tenant A Salon", "email": emailA, "password": PW,
        "phone": ph(1), "business_type_id": 1})
    bA, _ = ok(r, "fixtures", "register A", codes=(200, 201))
    F["A_email"] = emailA
    F["A_token"] = bA["access_token"]
    F["A_refresh"] = bA["refresh_token"]
    me, _ = ok(call("GET", "/auth/me", token=F["A_token"]), "fixtures", "me A")
    F["A_business_id"] = me["business_id"]

    # ---- Tenant B (owner) — for isolation tests ----
    emailB = f"ownerB_{RUN_TAG}@qa-booklync.com"
    r = call("POST", "/auth/register", json_body={
        "name": "Tenant B Salon", "email": emailB, "password": PW,
        "phone": ph(2), "business_type_id": 1})
    bB, _ = ok(r, "fixtures", "register B", codes=(200, 201))
    F["B_email"] = emailB
    F["B_token"] = bB["access_token"]
    meB, _ = ok(call("GET", "/auth/me", token=F["B_token"]), "fixtures", "me B")
    F["B_business_id"] = meB["business_id"]

    tA = F["A_token"]

    # ---- Agents (branches) for A ----
    a1, _ = ok(call("POST", "/agents/", token=tA, json_body={
        "agent_name": "Main Branch", "phone_number": ph(11),
        "location_name": "Downtown", "timezone": "UTC", "model": "claude-3-haiku-20240307",
        "currency": "USD", "tax_rate": 10, "tax_inclusive": False}), "fixtures", "create agent A1")
    F["A1"] = a1["id"]

    a2, _ = ok(call("POST", "/agents/", token=tA, json_body={
        "agent_name": "Second Branch", "phone_number": ph(12),
        "location_name": "Uptown", "timezone": "America/New_York", "model": "claude-3-haiku-20240307",
        "currency": "USD", "tax_rate": 0, "tax_inclusive": False}), "fixtures", "create agent A2")
    F["A2"] = a2["id"]

    # extreme-timezone branch for tz-duality tests (UTC+14)
    a3, _ = ok(call("POST", "/agents/", token=tA, json_body={
        "agent_name": "Island Branch", "phone_number": ph(13),
        "location_name": "Kiritimati", "timezone": "Pacific/Kiritimati",
        "model": "claude-3-haiku-20240307", "currency": "USD"}), "fixtures", "create agent A3 (UTC+14)")
    F["A3"] = a3["id"]

    # agent for B
    b1, _ = ok(call("POST", "/agents/", token=F["B_token"], json_body={
        "agent_name": "B Branch", "phone_number": ph(21),
        "location_name": "B-town", "timezone": "UTC", "model": "claude-3-haiku-20240307"}),
        "fixtures", "create agent B1")
    F["B1"] = b1["id"]

    # ---- Activate agents (created inactive by design) so public endpoints work ----
    for ag in (F["A1"], F["A2"], F["A3"]):
        ok(call("PATCH", f"/agents/{ag}/activate", token=tA), "fixtures", f"activate agent {ag}")
    ok(call("PATCH", f"/agents/{F['B1']}/activate", token=F["B_token"]), "fixtures", "activate agent B1")

    # ---- Business hours for A1, A2, A3: every day 09:00-17:00 ----
    for ag in (F["A1"], F["A2"], F["A3"]):
        hours = [{"day_of_week": d, "open_time": "09:00:00", "closed_time": "17:00:00"} for d in range(7)]
        ok(call("POST", "/business-hours/", token=tA, json_body={"agent_id": ag, "business_hours": hours}),
           "fixtures", f"business hours agent {ag}")

    # ---- Employees ----
    e1, _ = ok(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [F["A1"]], "name": "Alice Stylist", "role": "Stylist",
        "phone": ph(31), "email": f"alice_{RUN_TAG}@qa-booklync.com"}), "fixtures", "create employee E1")
    F["E1"] = e1["id"]

    e2, _ = ok(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [F["A1"], F["A2"]], "name": "Bob Multi", "role": "Barber"}),
        "fixtures", "create employee E2 (multi-branch)")
    F["E2"] = e2["id"]

    # working hours for E1 + E2 at A1 every day 09:00-17:00
    wh = [{"day_of_week": d, "start_time": "09:00:00", "end_time": "17:00:00"} for d in range(7)]
    ok(call("POST", f"/employees/{F['E1']}/working-hours", token=tA, params={"agent_id": F["A1"]},
            json_body={"working_hours": wh}), "fixtures", "E1 working hours A1")
    ok(call("POST", f"/employees/{F['E2']}/working-hours", token=tA, params={"agent_id": F["A1"]},
            json_body={"working_hours": wh}), "fixtures", "E2 working hours A1")

    # ---- Services ----
    # flat-rate service with prep times
    s1, _ = ok(call("POST", "/services/", token=tA, json_body={
        "agent_id": F["A1"], "name": "Haircut", "description": "basic",
        "duration_minutes": 30, "price": 40, "preparation_before_minutes": 10,
        "preparation_after_minutes": 10, "color": "#FF5733"}), "fixtures", "create service S1 (flat+prep)")
    F["S1"] = s1["id"]

    # variation-based service
    s2, _ = ok(call("POST", "/services/", token=tA, json_body={
        "agent_id": F["A1"], "name": "Coloring",
        "variations": [{"name": "Short", "price": 80, "duration_minutes": 60},
                       {"name": "Long", "price": 120, "duration_minutes": 90}]}),
        "fixtures", "create service S2 (variations)")
    F["S2"] = s2["id"]
    F["S2_var_short"] = s2["variations"][0]["id"]
    F["S2_var_long"] = s2["variations"][1]["id"]

    # service with no duration and no variations? Not allowed on create. Make a plain flat S3 (no prep)
    s3, _ = ok(call("POST", "/services/", token=tA, json_body={
        "agent_id": F["A1"], "name": "Shave", "duration_minutes": 20, "price": 25}),
        "fixtures", "create service S3 (flat)")
    F["S3"] = s3["id"]

    # capabilities: E1 can do S1, S2, S3 ; E2 can do S1
    for sid in (F["S1"], F["S2"], F["S3"]):
        ok(call("POST", f"/employees/{F['E1']}/capabilities", token=tA, params={"agent_id": F["A1"]},
                json_body={"service_id": sid}), "fixtures", f"E1 capability service {sid}")
    ok(call("POST", f"/employees/{F['E2']}/capabilities", token=tA, params={"agent_id": F["A1"]},
            json_body={"service_id": F["S1"]}), "fixtures", "E2 capability S1")

    # ---- Customer ----
    c1, _ = ok(call("POST", "/customers/", token=tA, json_body={
        "name": "Carol Customer", "phone": ph(41), "email": f"carol_{RUN_TAG}@qa-booklync.com"}),
        "fixtures", "create customer C1")
    F["C1"] = c1["id"]

    # ---- DB-seeded limited users ----
    recep_email = f"recep_{RUN_TAG}@qa-booklync.com"
    seed_user(F["A_business_id"], F["A1"], "receptionist", recep_email, PW)
    F["recep_token"] = _login(recep_email, PW)
    if not F["recep_token"]:
        finding("HIGH", "fixtures", "could not log in seeded receptionist", recep_email)

    staff_email = f"staff_{RUN_TAG}@qa-booklync.com"
    seed_user(F["A_business_id"], F["A1"], "staff", staff_email, PW, employee_id=F["E1"])
    F["staff_token"] = _login(staff_email, PW)
    if not F["staff_token"]:
        finding("HIGH", "fixtures", "could not log in seeded staff user", staff_email)

    # capture the limited users' actual permission sets (avoid false RBAC findings)
    rp = body(call("GET", "/auth/me", token=F["recep_token"])) or {}
    F["recep_perms"] = rp.get("permissions", [])
    sp = body(call("GET", "/auth/me", token=F["staff_token"])) or {}
    F["staff_perms"] = sp.get("permissions", [])

    # a useful future weekday date in UTC (agent A1 is UTC)
    base = datetime.datetime.utcnow() + datetime.timedelta(days=7)
    F["future_date"] = base.strftime("%Y-%m-%d")
    F["future_dt_10"] = base.strftime("%Y-%m-%dT10:00:00+00:00")
    F["future_dt_11"] = base.strftime("%Y-%m-%dT11:00:00+00:00")
    F["future_dt_14"] = base.strftime("%Y-%m-%dT14:00:00+00:00")
    F["past_dt"] = (datetime.datetime.utcnow() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT10:00:00+00:00")
    return F
