"""Users & Roles: catalogue, user admin (manage_users), branch-roles, accept-invite (DB-seeded)."""
import secrets
from core import call, body, ok, reject, finding, seed_invite, RUN_TAG

A = "users"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A1, A2, B1 = F["A1"], F["A2"], F["B1"]

    # catalogue (any auth)
    ok(call("GET", "/users/roles", token=tA), A, "GET /roles")
    ok(call("GET", "/users/permissions", token=tA), A, "GET /permissions")

    # list users (manage_users; owner-only by default)
    lst, good = ok(call("GET", "/users/", token=tA), A, "list users (owner)")
    if "manage_users" not in F["recep_perms"]:
        reject(call("GET", "/users/", token=tR), A, "receptionist list users", {403},
               expect_code="PERMISSION_DENIED")
    # B is its own owner -> 200 is correct; the real test is that the lists DON'T overlap
    ok(call("GET", "/users/", token=tB), A, "B lists its own users")
    lb = body(call("GET", "/users/", token=tB))
    if isinstance(lb, dict) and isinstance(lst, dict):
        a_ids = {u["id"] for u in lst.get("users", [])}
        b_ids = {u["id"] for u in lb.get("users", [])}
        if a_ids & b_ids:
            finding("CRITICAL", A, "TENANT LEAK: user lists overlap across tenants", f"{a_ids & b_ids}", "GET /")

    # ---- accept-invite via DB-seeded invite (no email) ----
    # disposable employee with email + no account
    einv = body(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [A1], "name": "Invitee", "email": f"invitee_{RUN_TAG}@qa-booklync.com"}))
    if isinstance(einv, dict) and einv.get("id"):
        raw = secrets.token_urlsafe(24)
        seed_invite(F["A_business_id"], einv["id"], [A1], "staff",
                    f"invitee_{RUN_TAG}@qa-booklync.com", raw)
        # bad token
        reject(call("POST", "/users/accept-invite", json_body={"token": "wrong-token", "password": "Password123!"}),
               A, "accept-invite bad token", {404, 400, 410})
        reject(call("POST", "/users/accept-invite", json_body={"token": raw, "password": "short"}),
               A, "accept-invite short password", {422})
        # valid accept
        acc, agood = ok(call("POST", "/users/accept-invite",
                        json_body={"token": raw, "password": "Password123!"}),
                        A, "accept-invite valid", codes=(200, 201))
        if agood and "access_token" not in (acc or {}):
            finding("HIGH", A, "accept-invite returned no token", acc, "accept-invite")
        # reusing the token -> already used / user exists
        reject(call("POST", "/users/accept-invite", json_body={"token": raw, "password": "Password123!"}),
               A, "accept-invite reuse token", {404, 409, 410, 400}, sev2xx="HIGH")

    # expired invite -> 410
    eexp = body(call("POST", "/employees/", token=tA, json_body={
        "agent_ids": [A1], "name": "ExpInvitee", "email": f"expinv_{RUN_TAG}@qa-booklync.com"}))
    if isinstance(eexp, dict) and eexp.get("id"):
        raw2 = secrets.token_urlsafe(24)
        seed_invite(F["A_business_id"], eexp["id"], [A1], "staff",
                    f"expinv_{RUN_TAG}@qa-booklync.com", raw2, expires_in_hours=-2, status="pending")
        reject(call("POST", "/users/accept-invite", json_body={"token": raw2, "password": "Password123!"}),
               A, "accept-invite expired", {410, 404, 400}, expect_code="INVITE_EXPIRED")

    # ---- user admin on the accepted user ----
    if good and isinstance(lst, dict):
        # find a non-owner user (the seeded receptionist/staff/invitee)
        target = next((u for u in lst.get("users", []) if not u.get("is_owner")), None)
        owner = next((u for u in lst.get("users", []) if u.get("is_owner")), None)
        if target:
            tid = target["id"]
            ok(call("PATCH", f"/users/{tid}/deactivate", token=tA), A, "deactivate user")
            ok(call("PATCH", f"/users/{tid}/activate", token=tA), A, "activate user")
            # add a branch role at A2, then remove
            ok(call("POST", f"/users/{tid}/branch-roles", token=tA,
                    json_body={"agent_id": A2, "role": "receptionist"}), A, "grant branch role @A2")
            ok(call("DELETE", f"/users/{tid}/branch-roles/{A2}", token=tA), A, "revoke branch role @A2")
            reject(call("POST", f"/users/{tid}/branch-roles", token=tA,
                    json_body={"agent_id": A2, "role": "owner"}), A, "grant role=owner (not invitable)",
                    {422, 400}, expect_code="ROLE_NOT_INVITABLE", sev2xx="HIGH")
            reject(call("POST", f"/users/{tid}/branch-roles", token=tA,
                    json_body={"agent_id": B1, "role": "receptionist"}), A,
                    "grant role at B's branch", {403, 404}, sev2xx="CRITICAL")
            # cross-tenant: B deactivates A's user
            reject(call("PATCH", f"/users/{tid}/deactivate", token=tB), A,
                   "B deactivates A's user", {403, 404}, sev2xx="CRITICAL")
        if owner:
            reject(call("PATCH", f"/users/{owner['id']}/deactivate", token=tA), A,
                   "deactivate the owner", {403}, expect_code="OWNER_PROTECTED", sev2xx="HIGH")
    reject(call("PATCH", "/users/99999999/deactivate", token=tA), A, "deactivate unknown user", {404})
