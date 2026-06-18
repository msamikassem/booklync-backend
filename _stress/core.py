"""
Core harness: HTTP helpers, finding logger, DB-seeding, shared fixtures.
Black-box tests the live server on :8000 against docs/api_reference.md.
"""
import json, os, datetime, uuid, logging, sys
import requests

# silence SQLAlchemy echo from the app engine (echo=True sets the .Engine logger to INFO)
for _ln in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
    logging.getLogger(_ln).setLevel(logging.WARNING)

BASE = "http://localhost:8000/api/v1"
ROOT = "http://localhost:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, BACKEND)  # so `app` imports work
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))  # so app.core.config.Settings finds env vars
except Exception:
    pass
JL   = os.path.join(HERE, "findings.jsonl")
RUN_TAG = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")

# ---- findings -------------------------------------------------------------
FINDINGS = []
SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

def finding(sev, area, title, detail="", ctx=""):
    rec = dict(sev=sev, area=area, title=title, detail=str(detail)[:2000], ctx=ctx)
    FINDINGS.append(rec)
    with open(JL, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"  [{sev:8}] {area}: {title}")
    return rec

# ---- http -----------------------------------------------------------------
def call(method, path, token=None, json_body=None, params=None, files=None,
         data=None, root=False, timeout=40):
    url = (ROOT if root else BASE) + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.request(method, url, headers=headers, json=json_body,
                             params=params, files=files, data=data, timeout=timeout)
        return r
    except Exception as e:
        # synthesize a pseudo-response so callers don't crash
        class _R:
            status_code = -1
            text = f"REQUEST-EXCEPTION: {e}"
            def json(self_): raise ValueError("no json")
        finding("CRITICAL", "transport", f"request crashed {method} {path}", str(e), path)
        return _R()

def body(r):
    try:
        return r.json()
    except Exception:
        return None

# ---- assertions (with auto-anomaly rules) ---------------------------------
def _auto(r, area, ctx, well_formed):
    if getattr(r, "status_code", 0) >= 500 and well_formed:
        finding("CRITICAL", area, f"5xx on well-formed request: {ctx}",
                f"status={r.status_code} body={body(r)}", ctx)

def ok(r, area, ctx, codes=(200, 201, 204)):
    """Expect success. 5xx or non-success => finding. Returns body."""
    _auto(r, area, ctx, True)
    if r.status_code not in codes:
        finding("HIGH", area, f"Expected success {codes} but got {r.status_code}: {ctx}",
                f"body={body(r)}", ctx)
        return body(r), False
    return body(r), True

def reject(r, area, ctx, codes, expect_code=None, well_formed=True, sev2xx="MEDIUM"):
    """Expect a 4xx rejection. Unexpected 2xx severity = sev2xx
    (use 'CRITICAL' for auth/tenant/RBAC bypass, 'HIGH' for broken business rules,
    'MEDIUM' for permissive input-validation gaps)."""
    _auto(r, area, ctx, well_formed)
    sc = r.status_code
    if 200 <= sc < 300:
        finding(sev2xx, area, f"Expected rejection {codes} but request SUCCEEDED ({sc}): {ctx}",
                f"body={body(r)}", ctx)
        return body(r), False
    cs = {codes} if isinstance(codes, int) else set(codes)
    if sc not in cs and sc < 500:
        finding("LOW", area, f"Rejected but with {sc}, expected {sorted(cs)}: {ctx}",
                f"body={body(r)}", ctx)
    b = body(r)
    if expect_code and isinstance(b, dict) and b.get("code") != expect_code:
        finding("LOW", area, f"Wrong error code: expected '{expect_code}' got '{b.get('code')}': {ctx}",
                f"status={sc} body={b}", ctx)
    return b, (sc in cs)

# ---- DB seeding (limited / staff users) -----------------------------------
def _db():
    from app.db.session import SessionLocal
    return SessionLocal()

def seed_user(business_id, agent_id, role_key, email, password, employee_id=None):
    """Insert a User + UserBranchRole directly; return (email, password)."""
    from app.models import models as m
    from app.core.security import hash_password
    db = _db()
    try:
        role = db.query(m.Role).filter(m.Role.key == role_key,
                                       m.Role.business_id.is_(None)).first()
        u = m.User(business_id=business_id, employee_id=employee_id, email=email,
                   password_hash=hash_password(password), is_owner=False, active=True)
        db.add(u); db.flush()
        db.add(m.UserBranchRole(user_id=u.id, agent_id=agent_id, role_id=role.id))
        db.commit()
        return u.id
    finally:
        db.close()

def seed_invite(business_id, employee_id, agent_ids, role_key, email, raw_token,
                expires_in_hours=48, status="pending"):
    """Insert a UserInvite row with a known raw token (so we can test accept-invite)."""
    from app.models import models as m
    from app.core.security import hash_password
    db = _db()
    try:
        role = db.query(m.Role).filter(m.Role.key == role_key,
                                       m.Role.business_id.is_(None)).first()
        token_hash = hash_password(raw_token)  # app verifies via verify_password (bcrypt)
        exp = datetime.datetime.utcnow() + datetime.timedelta(hours=expires_in_hours)
        inv = m.UserInvite(agent_ids=agent_ids, role_id=role.id, employee_id=employee_id,
                           email=email, token_hash=token_hash, status=status, expires_at=exp)
        db.add(inv); db.commit()
        return inv.id
    finally:
        db.close()

def invite_token_hash_scheme():
    """Return how the app hashes invite tokens, so seed_invite matches accept-invite."""
    import inspect
    from app.services import user_service
    return inspect.getsource(user_service)
