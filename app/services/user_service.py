"""
app/services/user_service.py

Business logic for the RBAC admin surface:

  • invite  — invite an existing employee to a dashboard login (email + role)
  • accept_invite — create the User + branch role from a valid invite token
  • list / activate / deactivate users
  • assign / revoke a user's role at a branch (multi-branch support)
  • list roles and permissions (for the invite/role-picker UI)

Invitations always originate from an existing Employee (which fixes the branch),
so the role is the only access decision the inviter makes.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.config import settings
from app.core.dependencies import CurrentUser
from app.core.exceptions import (
    AppException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.core.security import hash_password, verify_password
from app.models.models import (
    AIAgent,
    Employee,
    InviteStatusEnum,
    Role,
    User,
    UserBranchRole,
    UserInvite,
)
from app.services import rbac_service
from app.services.token_service import issue_tokens
from app.utils.email_utils import send_invite_email

# Roles that can be granted through the invite / assignment flow. The owner role
# is reserved for the account that registered the business.
INVITABLE_ROLE_KEYS = {rbac.ROLE_BRANCH_MANAGER, rbac.ROLE_RECEPTIONIST, rbac.ROLE_STAFF}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_employee_in_tenant(db: Session, employee_id: int, business_id: int) -> tuple[Employee, AIAgent]:
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found", code="NOT_FOUND")
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()
    if not agent or agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this employee")
    return emp, agent


def _resolve_invitable_role(db: Session, business_id: int, role_key: str) -> Role:
    if role_key not in INVITABLE_ROLE_KEYS:
        raise AppException(
            detail=f"Role '{role_key}' cannot be assigned. Choose one of: "
                   f"{', '.join(sorted(INVITABLE_ROLE_KEYS))}.",
            code="ROLE_NOT_INVITABLE",
            status_code=422,
        )
    return rbac_service.get_system_role(db, role_key)


# ─────────────────────────────────────────────────────────────────────────────
# INVITE
# ─────────────────────────────────────────────────────────────────────────────

def send_invite(
    db: Session,
    current: CurrentUser,
    employee_id: int,
    role_key: str,
    email: Optional[str] = None,
) -> dict:
    """
    Invite an employee to create a dashboard login with the given role.

    Idempotent: refreshes a live pending invite rather than creating duplicates.
    Caller must hold `invite_users` at the employee's branch (enforced here).
    """
    emp, agent = _load_employee_in_tenant(db, employee_id, current.business_id)
    current.require(rbac.P_INVITE_USERS, agent_id=agent.id)

    role = _resolve_invitable_role(db, current.business_id, role_key)

    target_email = email or emp.email
    if not target_email:
        raise AppException(
            detail="Employee has no email address. Provide one in the request body.",
            code="EMAIL_REQUIRED",
            status_code=422,
        )
    if email and email != emp.email:
        emp.email = email

    if emp.user:
        raise ConflictException(
            detail="This employee already has a dashboard account",
            code="USER_EXISTS",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.INVITE_EXPIRE_HOURS)
    raw_token = secrets.token_urlsafe(32)

    existing = (
        db.query(UserInvite)
        .filter(
            UserInvite.employee_id == employee_id,
            UserInvite.status == InviteStatusEnum.pending,
            UserInvite.expires_at > now,
        )
        .first()
    )
    if existing:
        existing.token_hash = hash_password(raw_token)
        existing.email = target_email
        existing.role_id = role.id
        existing.agent_id = agent.id
        existing.expires_at = expires_at
    else:
        db.add(UserInvite(
            agent_id=agent.id,
            role_id=role.id,
            employee_id=employee_id,
            email=target_email,
            token_hash=hash_password(raw_token),
            status=InviteStatusEnum.pending,
            expires_at=expires_at,
        ))
    db.commit()

    send_invite_email(
        to_email=target_email,
        invite_token=raw_token,
        employee_name=emp.name,
        business_name=agent.business.name if agent.business else "Your employer",
        api_base_url=settings.API_BASE_URL,
    )

    logger.info(f"Invite sent to {target_email} (employee={employee_id}, role={role_key})")
    return {
        "message": "Invitation sent",
        "email": target_email,
        "role": role.key,
        "expires_at": expires_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ACCEPT INVITE  (public — creates the User + branch role, then logs in)
# ─────────────────────────────────────────────────────────────────────────────

def accept_invite(db: Session, token: str, password: str) -> dict:
    now = datetime.now(timezone.utc)

    invite = None
    for candidate in db.query(UserInvite).filter(UserInvite.status == InviteStatusEnum.pending).all():
        if verify_password(token, candidate.token_hash):
            invite = candidate
            break

    if not invite:
        raise NotFoundException(detail="Invalid or expired invitation link", code="INVITE_NOT_FOUND")

    if invite.expires_at.replace(tzinfo=timezone.utc) < now:
        invite.status = InviteStatusEnum.expired
        db.commit()
        raise AppException(
            detail="This invitation has expired. Ask your manager to send a new one.",
            code="INVITE_EXPIRED",
            status_code=410,
        )

    emp = db.query(Employee).filter(Employee.id == invite.employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found")
    if emp.user:
        raise ConflictException(detail="A dashboard account already exists for this employee", code="USER_EXISTS")
    if db.query(User).filter(User.email == invite.email).first():
        raise ConflictException(
            detail="An account with this email already exists. Ask the owner to add a branch to it instead.",
            code="EMAIL_TAKEN",
        )

    agent = db.query(AIAgent).filter(AIAgent.id == invite.agent_id).first()

    user = User(
        business_id=agent.business_id,
        employee_id=emp.id,
        email=invite.email,
        password_hash=hash_password(password),
        is_owner=False,
        active=True,
    )
    db.add(user)
    db.flush()

    db.add(UserBranchRole(user_id=user.id, agent_id=invite.agent_id, role_id=invite.role_id))
    invite.status = InviteStatusEnum.accepted

    tokens = issue_tokens(db, user)
    db.commit()

    logger.info(f"Dashboard account created for employee={emp.id} (user={user.id}, email={invite.email})")
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# LIST / MANAGE USERS  (owner-level: requires manage_users)
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_user(user: User) -> dict:
    branches = []
    if user.is_owner:
        # Owners implicitly cover every branch; we surface that in /auth/me rather
        # than here to keep the user list compact.
        pass
    for ubr in user.branch_roles:
        branches.append({
            "agent_id": ubr.agent_id,
            "agent_name": ubr.agent.agent_name,
            "role_key": ubr.role.key,
            "role_name": ubr.role.name,
        })
    return {
        "id": user.id,
        "email": user.email,
        "is_owner": user.is_owner,
        "active": user.active,
        "employee_id": user.employee_id,
        "employee_name": user.employee.name if user.employee else None,
        "branches": branches,
    }


def list_users(db: Session, current: CurrentUser) -> dict:
    current.require(rbac.P_MANAGE_USERS)
    users = (
        db.query(User)
        .filter(User.business_id == current.business_id)
        .order_by(User.is_owner.desc(), User.email)
        .all()
    )
    return {"users": [_serialize_user(u) for u in users], "total": len(users)}


def _load_user_in_tenant(db: Session, user_id: int, business_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.business_id == business_id).first()
    if not user:
        raise NotFoundException(detail="User not found", code="USER_NOT_FOUND")
    return user


def set_user_active(db: Session, current: CurrentUser, user_id: int, active: bool) -> dict:
    current.require(rbac.P_MANAGE_USERS)
    user = _load_user_in_tenant(db, user_id, current.business_id)
    if user.is_owner:
        raise ForbiddenException(detail="The owner account cannot be deactivated", code="OWNER_PROTECTED")
    user.active = active
    if not active:
        user.refresh_token = None  # force logout
    db.commit()
    logger.info(f"User {user_id} active={active} by user={current.id}")
    return _serialize_user(user)


def assign_branch_role(db: Session, current: CurrentUser, user_id: int, agent_id: int, role_key: str) -> dict:
    """Grant a user a role at an additional branch (multi-branch assignment)."""
    current.require(rbac.P_MANAGE_USERS)
    user = _load_user_in_tenant(db, user_id, current.business_id)
    if user.is_owner:
        raise ForbiddenException(detail="The owner already has access to every branch", code="OWNER_PROTECTED")

    agent = db.query(AIAgent).filter(AIAgent.id == agent_id, AIAgent.business_id == current.business_id).first()
    if not agent:
        raise NotFoundException(detail="Branch not found", code="AGENT_NOT_FOUND")

    role = _resolve_invitable_role(db, current.business_id, role_key)

    existing = (
        db.query(UserBranchRole)
        .filter(UserBranchRole.user_id == user_id, UserBranchRole.agent_id == agent_id)
        .first()
    )
    if existing:
        existing.role_id = role.id
    else:
        db.add(UserBranchRole(user_id=user_id, agent_id=agent_id, role_id=role.id))
    db.commit()
    db.refresh(user)
    logger.info(f"Assigned role={role_key} to user={user_id} at branch={agent_id}")
    return _serialize_user(user)


def revoke_branch_role(db: Session, current: CurrentUser, user_id: int, agent_id: int) -> dict:
    current.require(rbac.P_MANAGE_USERS)
    user = _load_user_in_tenant(db, user_id, current.business_id)
    ubr = (
        db.query(UserBranchRole)
        .filter(UserBranchRole.user_id == user_id, UserBranchRole.agent_id == agent_id)
        .first()
    )
    if not ubr:
        raise NotFoundException(detail="This user has no role at that branch", code="ASSIGNMENT_NOT_FOUND")
    db.delete(ubr)
    db.commit()
    logger.info(f"Revoked branch={agent_id} role from user={user_id}")
    return {"message": "Branch access revoked"}


# ─────────────────────────────────────────────────────────────────────────────
# ROLES & PERMISSIONS CATALOGUE
# ─────────────────────────────────────────────────────────────────────────────

def list_roles(db: Session, current: CurrentUser) -> dict:
    """System presets + this tenant's custom roles (for the role-picker UI)."""
    roles = (
        db.query(Role)
        .filter((Role.business_id.is_(None)) | (Role.business_id == current.business_id))
        .order_by(Role.is_system.desc(), Role.name)
        .all()
    )
    out = []
    for r in roles:
        out.append({
            "id": r.id,
            "key": r.key,
            "name": r.name,
            "description": r.description,
            "is_system": r.is_system,
            "permissions": sorted({rp.permission.key for rp in r.permissions}),
        })
    return {"roles": out}


def list_permissions() -> dict:
    return {"permissions": list(rbac.PERMISSIONS)}
