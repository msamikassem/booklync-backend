"""Availability: /date, /check, /public/date, /time."""
from core import call, body, ok, reject, finding

A = "availability"

def run(F):
    tA, tB = F["A_token"], F["B_token"]
    A1, B1 = F["A1"], F["B1"]
    d = F["future_date"]

    # /date — flat service
    av, good = ok(call("GET", "/availability/date", token=tA,
                  params={"agent_id": A1, "service_id": F["S1"], "date": d}), A, "/date flat service")
    if good and "available_slots" not in (av or {}):
        finding("MEDIUM", A, "/date missing available_slots", list(av or {}), "/date")
    # /date with employee filter
    ok(call("GET", "/availability/date", token=tA,
       params={"agent_id": A1, "service_id": F["S1"], "date": d, "employee_id": F["E1"]}), A, "/date with employee")
    # /date variation service (no variation_id -> uses shortest)
    ok(call("GET", "/availability/date", token=tA,
       params={"agent_id": A1, "service_id": F["S2"], "date": d}), A, "/date variation service (default)")
    ok(call("GET", "/availability/date", token=tA,
       params={"agent_id": A1, "service_id": F["S2"], "date": d, "variation_id": F["S2_var_long"]}), A,
       "/date variation service (explicit)")

    # validation / errors
    reject(call("GET", "/availability/date", token=tA, params={"agent_id": A1, "service_id": F["S1"]}), A,
           "/date missing date", {422})
    reject(call("GET", "/availability/date", token=tA,
           params={"agent_id": A1, "service_id": F["S1"], "date": "not-a-date"}), A, "/date bad date", {422, 400})
    reject(call("GET", "/availability/date", token=tA,
           params={"agent_id": A1, "service_id": 99999999, "date": d}), A, "/date unknown service", {404, 400})
    reject(call("GET", "/availability/date", token=tB,
           params={"agent_id": A1, "service_id": F["S1"], "date": d}), A,
           "B queries A's availability", {403, 404}, sev2xx="CRITICAL")
    # service from another branch (B1) with A1 -> mismatch
    reject(call("GET", "/availability/date", token=tA,
           params={"agent_id": B1, "service_id": F["S1"], "date": d}), A,
           "A1 service against B branch", {403, 404}, sev2xx="CRITICAL")

    # /check a concrete start_time
    chk, good = ok(call("GET", "/availability/check", token=tA,
        params={"agent_id": A1, "service_id": F["S1"], "start_time": F["future_dt_10"]}), A, "/check open slot")
    if good and chk.get("available") is not True:
        finding("LOW", A, "/check reports unavailable for a presumably-open slot",
                f"resp={chk}", "/check")
    reject(call("GET", "/availability/check", token=tA,
           params={"agent_id": A1, "service_id": F["S1"], "start_time": "bad"}), A, "/check bad start_time", {422, 400})

    # /public/date (no auth)
    ok(call("GET", "/availability/public/date",
       params={"agent_id": A1, "service_id": F["S1"], "date": d}), A, "/public/date (public)")

    # /time
    t, good = ok(call("GET", "/availability/time", token=tA, params={"agent_id": A1}), A, "/time")
    if good:
        for k in ("timezone", "current_time", "utc_time", "is_open"):
            if k not in (t or {}):
                finding("LOW", A, f"/time missing field {k}", list(t or {}), "/time")
    reject(call("GET", "/availability/time", token=tB, params={"agent_id": A1}), A,
           "B queries A's /time", {403, 404}, sev2xx="CRITICAL")
