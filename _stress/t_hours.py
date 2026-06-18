"""Business hours + branch-wide days-off (prefix /api/v1)."""
from core import call, body, ok, reject, finding

A = "hours"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A2, B1 = F["A2"], F["B1"]   # use A2 so we don't disturb A1's booking hours
    q = {"agent_id": A2}

    ok(call("GET", "/business-hours/", token=tA, params=q), A, "get business hours")
    # replace whole week
    hrs = [{"day_of_week": d, "open_time": "08:00:00", "closed_time": "18:00:00"} for d in range(7)]
    bh, good = ok(call("POST", "/business-hours/", token=tA, json_body={"agent_id": A2, "business_hours": hrs}),
                  A, "replace business hours")
    # validation
    reject(call("POST", "/business-hours/", token=tA, json_body={"agent_id": A2,
            "business_hours": [{"day_of_week": 10, "open_time": "08:00:00", "closed_time": "18:00:00"}]}),
           A, "business hours bad day_of_week", {422, 400})
    reject(call("POST", "/business-hours/", token=tA, json_body={"agent_id": A2,
            "business_hours": [{"day_of_week": 1, "open_time": "18:00:00", "closed_time": "08:00:00"}]}),
           A, "business hours close<open", {422, 400, 409}, sev2xx="HIGH")
    reject(call("GET", "/business-hours/", token=tA), A, "business hours missing agent_id", {422})
    reject(call("POST", "/business-hours/", token=tB, json_body={"agent_id": A2, "business_hours": hrs}),
           A, "B writes A's business hours", {403, 404}, sev2xx="CRITICAL")
    if "manage_schedules" not in F["recep_perms"]:
        reject(call("POST", "/business-hours/", token=tR, json_body={"agent_id": A2, "business_hours": hrs}),
               A, "receptionist writes business hours", {403})
    # delete one day
    if good and bh.get("business_hours"):
        did = bh["business_hours"][0]["id"]
        ok(call("DELETE", f"/business-hours/{did}", token=tA, params=q), A, "delete one business-hour day")

    # ---- branch-wide days off ----
    do, good = ok(call("POST", "/days-off/", token=tA, json_body={"agent_id": A2, "name": "Holiday",
            "start_date": "2026-12-25", "end_date": "2026-12-25"}), A, "create branch day-off", codes=(200, 201))
    ok(call("GET", "/days-off/", token=tA, params=q), A, "list branch days off")
    reject(call("POST", "/days-off/", token=tA, json_body={"agent_id": A2, "name": "Bad",
            "start_date": "2026-12-26", "end_date": "2026-12-24"}), A, "branch day-off end<start", {422, 400}, sev2xx="HIGH")
    reject(call("POST", "/days-off/", token=tB, json_body={"agent_id": A2, "name": "x",
            "start_date": "2026-12-25", "end_date": "2026-12-25"}), A, "B writes A's days off", {403, 404}, sev2xx="CRITICAL")
    if good and isinstance(do, dict) and do.get("id"):
        ok(call("DELETE", f"/days-off/{do['id']}", token=tA, params=q), A, "delete branch day-off")
