"""Employees: CRUD, branch assign/unassign, schedules, capabilities, invite/account guards."""
from core import call, body, ok, reject, finding, RUN_TAG

A = "employees"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A1, A2, B1 = F["A1"], F["A2"], F["B1"]
    P = RUN_TAG[-6:]

    # ---- list / get ----
    lst, good = ok(call("GET", "/employees/", token=tA, params={"agent_id": A1}), A, "list emps A1")
    if good and "employees" not in lst:
        finding("MEDIUM", A, "employee list missing 'employees' key", list(lst), "GET /")
    reject(call("GET", "/employees/", token=tA), A, "list emps missing agent_id", {422})
    ok(call("GET", "/employees/", token=tA, params={"agent_id": A1, "bookable_only": True}), A,
       "list bookable_only")
    ok(call("GET", f"/employees/{F['E1']}", token=tA, params={"agent_id": A1}), A, "get E1")
    reject(call("GET", "/employees/99999999", token=tA, params={"agent_id": A1}), A,
           "get unknown emp", {404, 403})
    reject(call("GET", f"/employees/{F['E1']}", token=tB, params={"agent_id": A1}), A,
           "B reads A's employee (via A1)", {403, 404}, sev2xx="CRITICAL")
    reject(call("GET", f"/employees/{F['E1']}", token=tA, params={"agent_id": B1}), A,
           "A reads E1 scoped to B's branch", {403, 404}, sev2xx="CRITICAL")

    # ---- create validation ----
    reject(call("POST", "/employees/", token=tA, json_body={"agent_ids": [], "name": "X"}), A,
           "create empty agent_ids", {422, 400})
    # duplicate agent_ids are intentionally DEDUPED server-side (not rejected) — verify that
    dup, dgood = ok(call("POST", "/employees/", token=tA, json_body={"agent_ids": [A1, A1], "name": "Dup"}),
                    A, "create duplicate agent_ids (deduped)", codes=(200, 201))
    if dgood and len(dup.get("branches", [])) != 1:
        finding("MEDIUM", A, "duplicate agent_ids not deduped to one branch",
                f"branches={dup.get('branches')}", "create dup")
    reject(call("POST", "/employees/", token=tA, json_body={"agent_ids": [A1]}), A,
           "create missing name", {422})
    reject(call("POST", "/employees/", token=tA, json_body={"agent_ids": [B1], "name": "X"}), A,
           "create emp at B's branch (cross-tenant agent_id)", {403, 404}, sev2xx="CRITICAL")
    # RBAC: receptionist lacks manage_employees
    reject(call("POST", "/employees/", token=tR, json_body={"agent_ids": [A1], "name": "NoPerm"}), A,
           "receptionist create employee", {403}, expect_code="PERMISSION_DENIED")

    # ---- disposable employee for mutation/destructive tests ----
    tmp, good = ok(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [A1, A2], "name": "Temp Worker", "role": "Temp"}), A, "create disposable emp")
    if not good:
        finding("HIGH", A, "could not create disposable employee; skipping mutation tests", "", "")
        _invite_account_guards(F, tA, tR, tB)
        return
    EID = tmp["id"]

    # profile update + per-branch can_receive_bookings
    ok(call("PUT", f"/employees/{EID}", token=tA, params={"agent_id": A1},
            json_body={"role": "Senior Temp"}), A, "update emp profile")
    ok(call("PUT", f"/employees/{EID}", token=tA, params={"agent_id": A1},
            json_body={"can_receive_bookings": False}), A, "set can_receive_bookings false @A1")
    # edit_employee_details: receptionist lacks it
    reject(call("PUT", f"/employees/{EID}", token=tR, params={"agent_id": A1},
                json_body={"role": "hax"}), A, "receptionist edit emp details", {403})

    # activate / deactivate (toggle_employee_status)
    ok(call("PATCH", f"/employees/{EID}/deactivate", token=tA, params={"agent_id": A1}), A,
       "deactivate emp @A1")
    ok(call("PATCH", f"/employees/{EID}/activate", token=tA, params={"agent_id": A1}), A,
       "activate emp @A1")

    # ---- working hours ----
    wh = [{"day_of_week": d, "start_time": "09:00:00", "end_time": "17:00:00"} for d in range(7)]
    ok(call("POST", f"/employees/{EID}/working-hours", token=tA, params={"agent_id": A1},
            json_body={"working_hours": wh}), A, "set working hours")
    ok(call("GET", f"/employees/{EID}/working-hours", token=tA, params={"agent_id": A1}), A,
       "get working hours")
    reject(call("POST", f"/employees/{EID}/working-hours", token=tA, params={"agent_id": A1},
            json_body={"working_hours": [{"day_of_week": 1, "start_time": "09:00:00", "end_time": "17:00:00"},
                                         {"day_of_week": 1, "start_time": "10:00:00", "end_time": "12:00:00"}]}),
           A, "working hours duplicate day", {422, 400, 409})
    reject(call("POST", f"/employees/{EID}/working-hours", token=tA, params={"agent_id": A1},
            json_body={"working_hours": [{"day_of_week": 9, "start_time": "09:00:00", "end_time": "17:00:00"}]}),
           A, "working hours invalid day_of_week", {422, 400})
    reject(call("POST", f"/employees/{EID}/working-hours", token=tA, params={"agent_id": A1},
            json_body={"working_hours": [{"day_of_week": 1, "start_time": "17:00:00", "end_time": "09:00:00"}]}),
           A, "working hours end<start", {422, 400, 409}, sev2xx="HIGH")

    # ---- blocked times ----
    bt, good = ok(call("POST", f"/employees/{EID}/blocked-times", token=tA, params={"agent_id": A1},
            json_body={"day_of_week": 2, "start_time": "12:00:00", "end_time": "13:00:00", "reason": "lunch"}),
           A, "create blocked time", codes=(200, 201))
    ok(call("GET", f"/employees/{EID}/blocked-times", token=tA, params={"agent_id": A1}), A,
       "get blocked times")
    if good and isinstance(bt, dict) and bt.get("id"):
        ok(call("DELETE", f"/employees/{EID}/blocked-times/{bt['id']}", token=tA, params={"agent_id": A1}),
           A, "delete blocked time")

    # ---- days off ----
    do, good = ok(call("POST", f"/employees/{EID}/days-off", token=tA, params={"agent_id": A1},
            json_body={"name": "Vacay", "start_date": "2026-12-24", "end_date": "2026-12-26"}),
           A, "create day off", codes=(200, 201))
    reject(call("POST", f"/employees/{EID}/days-off", token=tA, params={"agent_id": A1},
            json_body={"name": "Bad", "start_date": "2026-12-26", "end_date": "2026-12-24"}),
           A, "day off end<start", {422, 400}, sev2xx="HIGH")
    ok(call("GET", f"/employees/{EID}/days-off", token=tA, params={"agent_id": A1}), A, "get days off")
    if good and isinstance(do, dict) and do.get("id"):
        ok(call("DELETE", f"/employees/{EID}/days-off/{do['id']}", token=tA, params={"agent_id": A1}),
           A, "delete day off")

    # ---- capabilities (exactly-one rule) ----
    ok(call("GET", f"/employees/{EID}/capabilities", token=tA, params={"agent_id": A1}), A,
       "get capabilities")
    reject(call("POST", f"/employees/{EID}/capabilities", token=tA, params={"agent_id": A1},
                json_body={"extra_price": 0}), A, "capability with no target", {422, 400})
    reject(call("POST", f"/employees/{EID}/capabilities", token=tA, params={"agent_id": A1},
                json_body={"service_id": F["S1"], "title_id": 1}), A,
           "capability with two targets", {422, 400, 404, 409})
    # can_receive_bookings is False @A1 (set above) -> capability add must 409 EMPLOYEE_NOT_BOOKABLE
    reject(call("POST", f"/employees/{EID}/capabilities", token=tA, params={"agent_id": A1},
                json_body={"service_id": F["S1"]}), A,
           "capability add to non-bookable emp", {409}, expect_code="EMPLOYEE_NOT_BOOKABLE", sev2xx="HIGH")
    # re-enable, then add a real capability + delete it
    ok(call("PUT", f"/employees/{EID}", token=tA, params={"agent_id": A1},
            json_body={"can_receive_bookings": True}), A, "re-enable bookings @A1")
    cap, good = ok(call("POST", f"/employees/{EID}/capabilities", token=tA, params={"agent_id": A1},
                json_body={"service_id": F["S1"]}), A, "add capability S1", codes=(200, 201))
    if good and isinstance(cap, dict) and cap.get("id"):
        ok(call("DELETE", f"/employees/{EID}/capabilities/{cap['id']}", token=tA, params={"agent_id": A1}),
           A, "delete capability")

    # ---- profile picture (multipart) ----
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    pp, pgood = ok(call("POST", f"/employees/{EID}/profile-picture", token=tA, params={"agent_id": A1},
                   files={"file": ("p.png", png, "image/png")}), A, "upload profile picture", codes=(200, 201))
    # non-image rejected
    reject(call("POST", f"/employees/{EID}/profile-picture", token=tA, params={"agent_id": A1},
           files={"file": ("x.txt", b"not-an-image", "text/plain")}), A,
           "upload non-image profile picture", {400, 415, 422}, sev2xx="MEDIUM")
    if pgood:
        ok(call("DELETE", f"/employees/{EID}/profile-picture", token=tA, params={"agent_id": A1}), A,
           "delete profile picture")

    # ---- branch assign / unassign ----
    reject(call("POST", f"/employees/{EID}/branches", token=tA, json_body={"agent_id": A1}), A,
           "add branch already-assigned", {409}, expect_code="ALREADY_ASSIGNED", sev2xx="HIGH")
    reject(call("POST", f"/employees/{EID}/branches", token=tA, json_body={"agent_id": B1}), A,
           "add B's branch to A's emp", {403, 404}, sev2xx="CRITICAL")
    ok(call("DELETE", f"/employees/{EID}/branches/{A2}", token=tA), A, "unassign one branch (A2)")
    reject(call("DELETE", f"/employees/{EID}/branches/{A1}", token=tA), A,
           "unassign last branch", {409}, expect_code="LAST_BRANCH", sev2xx="HIGH")

    # ---- delete ----
    # E2 is multi-branch (A1+A2) -> delete must refuse
    reject(call("DELETE", f"/employees/{F['E2']}", token=tA, params={"agent_id": A1}), A,
           "delete multi-branch emp", {409}, expect_code="EMPLOYEE_MULTI_BRANCH", sev2xx="HIGH")
    # disposable EID is now single-branch (A1) -> delete OK
    ok(call("DELETE", f"/employees/{EID}", token=tA, params={"agent_id": A1}), A, "delete single-branch emp")
    reject(call("GET", f"/employees/{EID}", token=tA, params={"agent_id": A1}), A,
           "get deleted emp", {404, 403})

    _invite_account_guards(F, tA, tR, tB)

def _invite_account_guards(F, tA, tR, tB):
    """Invite/account: only the guards that return BEFORE sending email (happy path skipped)."""
    A1 = F["A1"]
    # invite with role=owner -> 422 ROLE_NOT_INVITABLE (pre-email)
    reject(call("POST", f"/employees/{F['E2']}/invite", token=tA,
                json_body={"role": "owner"}), A, "invite role=owner", {422, 400},
           expect_code="ROLE_NOT_INVITABLE", sev2xx="HIGH")
    # invite unknown employee -> 404 (pre-email)
    reject(call("POST", "/employees/99999999/invite", token=tA, json_body={"role": "staff"}), A,
           "invite unknown employee", {404})
    # invite cross-tenant employee -> 403 (pre-email)
    reject(call("POST", f"/employees/{F['E1']}/invite", token=tB, json_body={"role": "staff"}), A,
           "B invites A's employee", {403, 404}, sev2xx="CRITICAL")
    finding("INFO", A, "invite send happy path SKIPPED (sends Resend email) per instruction",
            "POST /{id}/invite success not exercised", "invite")
    # account revoke guards (DB-only; safe). E2 has no dashboard account -> 404
    reject(call("DELETE", f"/employees/{F['E2']}/account", token=tA), A,
           "revoke account on emp w/o account", {404}, expect_code="NO_DASHBOARD_ACCOUNT")
    # receptionist lacks manage_users
    reject(call("DELETE", f"/employees/{F['E1']}/account", token=tR), A,
           "receptionist revoke account", {403})
