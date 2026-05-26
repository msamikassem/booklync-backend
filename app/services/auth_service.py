"""
app/services/auth_service.py

Authentication business logic for the unified RBAC user model.

There is a single login identity — `User` — for everyone (owner, branch
manager, receptionist, staff). Registration creates a tenant `Business` plus
its owner `User`. All other users are created through the invite flow
(see user_service / accept_invite). Everyone logs in here at /auth/login.
"""

from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from loguru import logger

from app.core import rbac
from app.models.models import AIAgent, Business, User, UserBranchRole
from app.schemas.auth import BusinessRegister, BusinessLogin
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
)
from app.core.exceptions import (
    ConflictException,
    UnauthorizedException,
    NotFoundException,
    BusinessInactiveException,
)
from app.services.token_service import issue_tokens


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER  (creates a tenant Business + its owner User)
# ─────────────────────────────────────────────────────────────────────────────

def register(db: Session, data: BusinessRegister) -> dict:
    if db.query(User).filter(User.email == data.email).first():
        raise ConflictException(detail="An account with this email already exists", code="EMAIL_TAKEN")

    if db.query(Business).filter(Business.phone == data.phone).first():
        raise ConflictException(detail="An account with this phone number already exists", code="PHONE_TAKEN")

    hashed = hash_password(data.password)

    business = Business(
        name=data.name,
        email=data.email,
        password_hash=hashed,   # tenant contact record; auth uses the User row
        phone=data.phone,
        business_type_id=data.business_type_id,
        subscription_plan="free",
        active=True,
    )
    db.add(business)
    db.flush()  # need business.id

    owner = User(
        business_id=business.id,
        email=data.email,
        password_hash=hashed,
        is_owner=True,
        active=True,
    )
    db.add(owner)
    db.flush()  # need owner.id for tokens

    tokens = issue_tokens(db, owner)
    db.commit()

    logger.info(f"New business + owner registered: {data.email} (business={business.id}, user={owner.id})")
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN  (single login page — any role)
# ─────────────────────────────────────────────────────────────────────────────

def login(db: Session, data: BusinessLogin) -> dict:
    user = db.query(User).filter(User.email == data.email).first()

    # Same error for "no such user" and "wrong password" — don't leak which.
    if not user or not verify_password(data.password, user.password_hash):
        raise UnauthorizedException(detail="Invalid email or password", code="INVALID_CREDENTIALS")

    if not user.active:
        raise UnauthorizedException(detail="This account is inactive", code="ACCOUNT_INACTIVE")

    if not user.business or not user.business.active:
        raise BusinessInactiveException()

    tokens = issue_tokens(db, user)
    user.last_login = func.now()
    db.commit()

    logger.info(f"User logged in: {data.email} (id={user.id}, owner={user.is_owner})")
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh_access_token(db: Session, refresh_token: str) -> dict:
    payload = decode_token(refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise UnauthorizedException(detail="Invalid or expired refresh token", code="INVALID_REFRESH_TOKEN")

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise UnauthorizedException(detail="User not found", code="USER_NOT_FOUND")

    if not user.refresh_token or not verify_password(refresh_token, user.refresh_token):
        raise UnauthorizedException(detail="Refresh token has been revoked", code="TOKEN_REVOKED")

    if not user.active:
        raise UnauthorizedException(detail="This account is inactive", code="ACCOUNT_INACTIVE")
    if not user.business or not user.business.active:
        raise BusinessInactiveException()

    access_token = create_access_token(subject=user.id)
    logger.info(f"Access token refreshed for user id={user.id}")
    return {"access_token": access_token, "token_type": "bearer"}


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

def logout(db: Session, user_id: int) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundException(detail="User not found")
    user.refresh_token = None
    db.commit()
    logger.info(f"User logged out: id={user_id}")
    return {"message": "Logged out successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# ME  (identity + access profile)
# ─────────────────────────────────────────────────────────────────────────────

def get_me(db: Session, user_id: int) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundException(detail="User not found")

    business = user.business
    branches: list[dict] = []

    if user.is_owner:
        all_perms = sorted(rbac.ALL_PERMISSION_KEYS)
        agents = db.query(AIAgent).filter(AIAgent.business_id == user.business_id).all()
        for a in agents:
            branches.append({
                "agent_id": a.id,
                "agent_name": a.agent_name,
                "role_key": rbac.ROLE_OWNER,
                "role_name": "Owner",
                "permissions": all_perms,
            })
        permissions = all_perms
    else:
        aggregated: set[str] = set()
        ubrs = db.query(UserBranchRole).filter(UserBranchRole.user_id == user.id).all()
        for ubr in ubrs:
            role = ubr.role
            perms = sorted({rp.permission.key for rp in role.permissions})
            aggregated.update(perms)
            branches.append({
                "agent_id": ubr.agent_id,
                "agent_name": ubr.agent.agent_name,
                "role_key": role.key,
                "role_name": role.name,
                "permissions": perms,
            })
        permissions = sorted(aggregated)

    return {
        "id": user.id,
        "email": user.email,
        "is_owner": user.is_owner,
        "employee_id": user.employee_id,
        "employee_name": user.employee.name if user.employee else None,
        "business_id": business.id,
        "business_name": business.name,
        "subscription_plan": business.subscription_plan,
        "permissions": permissions,
        "branches": branches,
    }
