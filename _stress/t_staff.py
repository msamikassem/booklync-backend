"""Staff own-scope surface (/staff/*). staff_token is linked to E1 at A1."""
from core import call, body, ok, reject, finding

A = "staff"

def run(F):
    tS, tR, tB = F["staff_token"], F["recep_token"], F["B_token"]
    A1, A2, B1 = F["A1"], F["A2"], F["B1"]

    me, good = ok(call("GET", "/staff/me", token=tS), A, "staff /me")
    if good:
        for k in ("employee_id", "agent_id", "branches", "business_id"):
            if k not in (me or {}):
                finding("MEDIUM", A, f"/staff/me missing '{k}'", list(me or {}), "/me")
    ok(call("GET", "/staff/time", token=tS), A, "staff /time")
    ok(call("GET", "/staff/business-hours", token=tS), A, "staff /business-hours")
    ok(call("GET", "/staff/working-hours", token=tS), A, "staff /working-hours")
    ok(call("GET", "/staff/blocked-times", token=tS), A, "staff /blocked-times")
    ok(call("GET", "/staff/days-off", token=tS), A, "staff /days-off")
    apts, good = ok(call("GET", "/staff/appointments", token=tS), A, "staff /appointments")

    # ?agent_id override: A1 is reachable; B1 and A2 are NOT (staff only at A1)
    ok(call("GET", "/staff/me", token=tS, params={"agent_id": A1}), A, "staff /me agent_id=A1")
    reject(call("GET", "/staff/me", token=tS, params={"agent_id": B1}), A,
           "staff /me agent_id=B1 (other tenant)", {403, 404}, expect_code="NO_BRANCH_ACCESS", sev2xx="CRITICAL")
    reject(call("GET", "/staff/appointments", token=tS, params={"agent_id": A2}), A,
           "staff /appointments agent_id=A2 (not in branch)", {403}, expect_code="NO_BRANCH_ACCESS", sev2xx="HIGH")

    # a user with no employee link -> NOT_AN_EMPLOYEE
    reject(call("GET", "/staff/me", token=tR), A, "non-employee user hits /staff/me", {403},
           expect_code="NOT_AN_EMPLOYEE")
    reject(call("GET", "/staff/me", token=tB), A, "owner B (no emp) hits /staff/me", {403})

    # PATCH own appointment status (allowed: completed/no_show/cancelled)
    own = None
    if good and apts and apts.get("appointments"):
        # find one that's still mutable (not already cancelled/completed)
        for a in apts["appointments"]:
            if a.get("status") in ("pending", "confirmed"):
                own = a["id"]; break
    if own:
        ok(call("PATCH", f"/staff/appointments/{own}/status", token=tS, json_body={"status": "no_show"}),
           A, "staff set own appt no_show")
        # disallowed status value
        reject(call("PATCH", f"/staff/appointments/{own}/status", token=tS, json_body={"status": "confirmed"}),
               A, "staff set disallowed status", {403, 400, 422}, expect_code="FORBIDDEN_STATUS", sev2xx="HIGH")
    else:
        finding("INFO", A, "no mutable own appointment found to test status PATCH", "", "status")
    reject(call("PATCH", "/staff/appointments/99999999/status", token=tS, json_body={"status": "completed"}),
           A, "staff status on unknown appt", {404})
