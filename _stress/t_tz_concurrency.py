"""Timezone day-boundary detonation (UTC+14 branch) + concurrency races (TOCTOU)."""
import datetime, concurrent.futures as cf
from collections import Counter
from core import call, body, ok, finding

A = "tz/concurrency"

def run(F):
    tA = F["A_token"]; A1, A3 = F["A1"], F["A3"]

    # ---------- TZ BOMB: A3 = Pacific/Kiritimati (UTC+14) ----------
    # Set up A3 with an employee + working hours + a flat service + capability.
    e = body(call("POST", "/employees/", token=tA, json_body={"agent_ids": [A3], "name": "Island Stylist"}))
    if isinstance(e, dict) and e.get("id"):
        eid = e["id"]
        call("POST", f"/employees/{eid}/working-hours", token=tA, params={"agent_id": A3},
             json_body={"working_hours": [{"day_of_week": d, "start_time": "09:00:00", "end_time": "17:00:00"}
                                          for d in range(7)]})
        svc = body(call("POST", "/services/", token=tA,
                   json_body={"agent_id": A3, "name": "Island Cut", "duration_minutes": 30, "price": 20}))
        sid = svc["id"] if isinstance(svc, dict) else None
        if sid:
            call("POST", f"/employees/{eid}/capabilities", token=tA, params={"agent_id": A3},
                 json_body={"service_id": sid})
            D = datetime.date.fromisoformat(F["future_date"]) + datetime.timedelta(days=50)
            # A3 LOCAL 09:00 == UTC 19:00 on the PREVIOUS calendar day
            start_utc = datetime.datetime(D.year, D.month, D.day, 9, 0) - datetime.timedelta(hours=14)
            start_iso = start_utc.strftime("%Y-%m-%dT%H:%M:00+00:00")
            r = call("POST", "/appointments/", token=tA, json_body={
                "agent_id": A3, "customer_id": F["C1"], "employee_id": eid, "service_id": sid,
                "start_time": start_iso, "source": "manual"})
            ab, booked = ok(r, A, f"book A3 local-09:00 (UTC {start_iso})", codes=(200, 201))
            if booked:
                Dl = D.isoformat(); Dm1 = (D - datetime.timedelta(days=1)).isoformat()
                def total(df, dt):
                    res = body(call("GET", "/appointments/", token=tA,
                               params={"agent_id": A3, "date_from": df, "date_to": dt}))
                    return res.get("total") if isinstance(res, dict) else None
                # 1) start_time must serialize as UTC (+00:00 / Z), NOT the host offset.
                st = ab.get("start_time") or ""
                sdt = None
                try:
                    sdt = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
                except Exception:
                    pass
                if sdt is not None and sdt.utcoffset() not in (datetime.timedelta(0), None):
                    finding("HIGH", A, "start_time not serialized as UTC for a UTC+14 branch",
                            f"start_time={st} (offset {sdt.utcoffset()})", "tz-serialization")
                # 2) The UTC instant is on the PREVIOUS calendar day (local 09:00 @ +14 = prev-day 19:00Z).
                #    The /appointments filter is UTC-based, so the appt is correctly returned for its
                #    UTC day, and (by design) NOT for the branch-local day unless the client widens +/-1d.
                utc_day = (sdt.astimezone(datetime.timezone.utc).date().isoformat() if sdt else Dm1)
                n_utc = total(f"{utc_day}T00:00:00+00:00", f"{utc_day}T23:59:59+00:00")
                n_local = total(f"{Dl}T00:00:00+00:00", f"{Dl}T23:59:59+00:00")
                n_wide  = total(f"{Dm1}T00:00:00+00:00", f"{Dl}T23:59:59+00:00")
                if (n_utc or 0) < 1:
                    finding("HIGH", A, "UTC date-range filter did not return the appt for its own UTC day",
                            f"utc_day={utc_day} total={n_utc}", "tz-filter")
                finding("INFO", A,
                        "tz day-boundary (by design): times are UTC; the UTC-day query returns the appt, "
                        "the branch-local-day query does not unless widened +/-1 day (frontend widens).",
                        f"start_time={st} utc_day_query={n_utc} local_day_query={n_local} widened={n_wide}",
                        "tz-day-boundary")

    # ---------- CONCURRENCY 1: simultaneous double-book of the SAME slot ----------
    DB = (datetime.date.fromisoformat(F["future_date"]) + datetime.timedelta(days=51)).isoformat()
    payload = {"agent_id": A1, "customer_id": F["C1"], "employee_id": F["E1"], "service_id": F["S1"],
               "start_time": f"{DB}T09:00:00+00:00", "source": "manual"}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(lambda _: call("POST", "/appointments/", token=tA, json_body=payload).status_code,
                            range(8)))
    created = sum(1 for c in codes if c in (200, 201))
    res = body(call("GET", "/appointments/", token=tA,
               params={"agent_id": A1, "date_from": f"{DB}T00:00:00+00:00", "date_to": f"{DB}T23:59:59+00:00"}))
    persisted = res.get("total") if isinstance(res, dict) else None
    if created > 1 or (persisted or 0) > 1:
        finding("CRITICAL", A,
                "DOUBLE-BOOK RACE: concurrent identical bookings both succeeded (TOCTOU in create_appointment)",
                f"8 concurrent POSTs -> {dict(Counter(codes))}; created={created}, persisted at slot={persisted}",
                "concurrency-doublebook")
    else:
        finding("INFO", A, "double-book under concurrency holds (exactly one created)",
                f"codes={dict(Counter(codes))} persisted={persisted}", "concurrency-doublebook")

    # ---------- CONCURRENCY 2: simultaneous double-payment settling the balance ----------
    apt = body(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": F["C1"], "employee_id": F["E1"], "service_id": F["S1"],
        "start_time": f"{DB}T11:00:00+00:00", "source": "manual"}))
    if isinstance(apt, dict) and apt.get("id"):
        call("PATCH", f"/appointments/{apt['id']}/status", token=tA, json_body={"status": "completed"})
        sales = body(call("GET", "/financials/sales", token=tA, params={"agent_id": A1}))
        sale = None
        if isinstance(sales, dict):
            sale = next((s for s in sales.get("sales", [])
                         if any(li.get("appointment_id") == apt["id"] for li in s.get("line_items", []))), None)
        if sale:
            SID = sale["id"]; tot = float(sale["total"])
            with cf.ThreadPoolExecutor(max_workers=6) as ex:
                pcodes = list(ex.map(
                    lambda _: call("POST", f"/financials/sales/{SID}/payments", token=tA,
                                   json_body={"amount": tot, "method": "cash"}).status_code, range(6)))
            final = body(call("GET", f"/financials/sales/{SID}", token=tA)) or {}
            paid = round(sum(float(p["amount"]) for p in final.get("payments", [])), 2)
            if paid > tot + 0.01:
                finding("MEDIUM", A,
                        "DOUBLE-PAYMENT RACE: concurrent full payments over-settled the sale "
                        "(no balance lock; multiple payments recorded beyond the total)",
                        f"total={tot}, sum(payments)={paid}, statuses={dict(Counter(pcodes))}, "
                        f"final status={final.get('status')}", "concurrency-payment")
            else:
                finding("INFO", A, "double-payment under concurrency holds (not over-settled)",
                        f"total={tot} paid={paid} codes={dict(Counter(pcodes))}", "concurrency-payment")
