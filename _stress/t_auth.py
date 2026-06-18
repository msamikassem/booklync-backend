"""Auth: /auth/register, /login, /refresh, /logout, /me + token edge cases."""
from core import call, body, ok, reject, finding, RUN_TAG

A = "auth"

def run(F):
    tA = F["A_token"]

    # ---- /me happy path + shape ----
    me, good = ok(call("GET", "/auth/me", token=tA), A, "GET /me owner")
    if good:
        for k in ("id", "email", "is_owner", "business_id", "permissions", "branches"):
            if k not in me:
                finding("MEDIUM", A, f"/me missing field '{k}'", f"keys={list(me)}", "GET /me")
        if me.get("is_owner") is not True:
            finding("HIGH", A, "owner /me is_owner not True", me, "GET /me")

    # ---- register validation matrix ----
    base = dict(name="X", email=f"v_{RUN_TAG}@qa-booklync.com", password="Password123!",
                phone=f"+1999{RUN_TAG[-7:]}", business_type_id=1)
    reject(call("POST", "/auth/register", json_body={**base, "password": "short"}), A,
           "register short password", {422})
    reject(call("POST", "/auth/register", json_body={**base, "email": "not-an-email"}), A,
           "register bad email", {422})
    reject(call("POST", "/auth/register", json_body={k: v for k, v in base.items() if k != "email"}),
           A, "register missing email", {422})
    reject(call("POST", "/auth/register", json_body={**base, "business_type_id": 999999}), A,
           "register unknown business_type_id", {400, 404, 409, 422})
    # duplicate email
    reject(call("POST", "/auth/register", json_body={**base, "email": F["A_email"],
                "phone": f"+1888{RUN_TAG[-7:]}"}), A, "register duplicate email", {409, 400, 422},
           sev2xx="CRITICAL")
    # duplicate phone (A's business phone) — using a fresh email
    reject(call("POST", "/auth/register", json_body={**base, "email": f"dup_{RUN_TAG}@qa-booklync.com",
                "phone": f"+1{RUN_TAG[-6:]}0001"}), A, "register duplicate phone", {409, 400, 422})

    # ---- login ----
    ok(call("POST", "/auth/login", json_body={"email": F["A_email"], "password": "Password123!"}),
       A, "login valid")
    reject(call("POST", "/auth/login", json_body={"email": F["A_email"], "password": "wrongpass"}),
           A, "login wrong password", {401, 400, 403})
    reject(call("POST", "/auth/login", json_body={"email": f"nobody_{RUN_TAG}@qa-booklync.com",
                "password": "Password123!"}), A, "login unknown email", {401, 400, 404, 403})
    reject(call("POST", "/auth/login", json_body={"email": F["A_email"]}), A,
           "login missing password", {422})
    # case-sensitivity of email login (informational)
    r = call("POST", "/auth/login", json_body={"email": F["A_email"].upper(), "password": "Password123!"})
    if r.status_code not in (200, 201):
        finding("INFO", A, "login is case-sensitive on email", f"upper-case email -> {r.status_code}",
                "login case")

    # ---- refresh ----
    rb, good = ok(call("POST", "/auth/refresh", json_body={"refresh_token": F["A_refresh"]}),
                  A, "refresh valid")
    if good and "access_token" not in (rb or {}):
        finding("HIGH", A, "refresh returned no access_token", rb, "refresh")
    reject(call("POST", "/auth/refresh", json_body={"refresh_token": "garbage.token.here"}),
           A, "refresh garbage token", {401, 400, 422})
    # access token used as refresh token must be rejected
    reject(call("POST", "/auth/refresh", json_body={"refresh_token": tA}), A,
           "access token accepted as refresh", {401, 400, 403, 422})

    # ---- token edge cases on a protected endpoint ----
    reject(call("GET", "/auth/me"), A, "no token -> /me", {401, 403})
    reject(call("GET", "/auth/me", token="garbage"), A, "garbage token -> /me", {401, 403})
    reject(call("GET", "/auth/me", token=tA + "x"), A, "tampered token -> /me", {401, 403})
    # an expired/none-alg forged token
    import base64, json as _j
    forged = ".".join([
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"sub":"1","type":"access"}').decode().rstrip("="), ""])
    reject(call("GET", "/auth/me", token=forged), A, "alg=none forged token", {401, 403},
           sev2xx="CRITICAL")

    # ---- logout ----
    ok(call("POST", "/auth/logout", token=tA), A, "logout")
    # NOTE: access token may remain valid until expiry (stateless JWT) — that's expected.
