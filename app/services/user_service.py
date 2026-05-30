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
from sqlalchemy.orm.attributes import flag_modified

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
    EmployeeBranch,
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

def _load_employee_in_tenant(
    db: Session, employee_id: int, business_id: int
) -> tuple[Employee, list[AIAgent]]:
    """
    Load an Employee and the list of branches it belongs to inside the caller's
    tenant. With multi-branch employees the second value is now a list (always
    non-empty for a valid in-tenant employee).
    """
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found", code="NOT_FOUND")
    agents = (
        db.query(AIAgent)
        .join(EmployeeBranch, EmployeeBranch.agent_id == AIAgent.id)
        .filter(
            EmployeeBranch.employee_id == employee_id,
            AIAgent.business_id == business_id,
        )
        .order_by(AIAgent.id)
        .all()
    )
    if not agents:
        raise ForbiddenException(detail="You do not have access to this employee")
    return emp, agents


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
    agent_ids: Optional[list[int]] = None,
) -> dict:
    """
    Invite an employee to create a dashboard login with the given role,
    optionally scoped to a subset of branches the employee belongs to.

    Defaults to every branch the employee is currently assigned to when
    `agent_ids` is omitted (or empty). The same role is applied at every
    selected branch on accept (one UserBranchRole per agent_id).

    Idempotent: refreshes a live pending invite rather than creating duplicates.
    Caller must hold `invite_users` at EVERY selected branch.
    """
    emp, agents = _load_employee_in_tenant(db, employee_id, current.business_id)
    employee_branch_ids = [a.id for a in agents]

    # Resolve the target branch list (defaults to the employee's full set).
    if agent_ids:
        # De-dupe while preserving order.
        seen: set[int] = set()
        target_ids: list[int] = []
        for aid in agent_ids:
            if aid in seen:
                continue
            seen.add(aid)
            target_ids.append(aid)
    else:
        target_ids = list(employee_branch_ids)

    if not target_ids:
        raise AppException(
            detail="Invite must cover at least one branch",
            code="VALIDATION_ERROR",
            status_code=422,
        )

    employee_branch_set = set(employee_branch_ids)
    for aid in target_ids:
        if aid not in employee_branch_set:
            raise ForbiddenException(
                detail=f"Employee is not assigned to branch id={aid}",
                code="BRANCH_FORBIDDEN",
            )
        current.require(rbac.P_INVITE_USERS, agent_id=aid)

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
        existing.agent_ids = target_ids
        # Per the project's JSONB rule: SQLAlchemy needs an explicit
        # flag_modified after replacing a JSONB column or the change can be
        # silently dropped on commit. (CLAUDE.md load-bearing rule.)
        flag_modified(existing, "agent_ids")
        existing.expires_at = expires_at
    else:
        db.add(UserInvite(
            agent_ids=target_ids,
            role_id=role.id,
            employee_id=employee_id,
            email=target_email,
            token_hash=hash_password(raw_token),
            status=InviteStatusEnum.pending,
            expires_at=expires_at,
        ))
    db.commit()

    # Pick any branch in the invite to populate the business-name email field.
    first_agent = agents[0]
    send_invite_email(
        to_email=target_email,
        invite_token=raw_token,
        employee_name=emp.name,
        business_name=first_agent.business.name if first_agent.business else "Your employer",
        api_base_url=settings.API_BASE_URL,
    )

    logger.info(
        f"Invite sent to {target_email} "
        f"(employee={employee_id}, role={role_key}, branches={target_ids})"
    )
    return {
        "message": "Invitation sent",
        "email": target_email,
        "role": role.key,
        "expires_at": expires_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CANCEL INVITE  (delete the live pending/expired invite for an employee)
# ─────────────────────────────────────────────────────────────────────────────

def cancel_invite(db: Session, current: CurrentUser, employee_id: int) -> dict:
    """
    Delete the most recent UserInvite for an employee. Requires
    `invite_users` at every branch the invite was about to grant access to —
    symmetric with `send_invite`. If no live invite exists, raises 404.
    The Employee row, profile, and schedule are untouched.
    """
    emp, _agents = _load_employee_in_tenant(db, employee_id, current.business_id)

    invite = (
        db.query(UserInvite)
        .filter(UserInvite.employee_id == emp.id)
        .order_by(UserInvite.created_at.desc())
        .first()
    )
    if invite is None:
        raise NotFoundException(
            detail="No invitation to cancel", code="INVITE_NOT_FOUND"
        )
    if invite.status == InviteStatusEnum.accepted:
        # An accepted invite is just history — there's no door to close.
        raise ConflictException(
            detail="This invitation has already been accepted; revoke the dashboard account instead",
            code="INVITE_ACCEPTED",
        )

    # Permission: the caller must hold invite_users at every branch the
    # invite would have covered (or owner via short-circuit).
    for aid in list(invite.agent_ids or []):
        current.require(rbac.P_INVITE_USERS, agent_id=aid)

    db.delete(invite)
    db.commit()

    logger.info(
        f"Invite cancelled for employee={employee_id} by user={current.id}"
    )
    return {"message": "Invitation cancelled"}


# ─────────────────────────────────────────────────────────────────────────────
# REVOKE DASHBOARD ACCOUNT  (delete the User; preserve the Employee)
# ─────────────────────────────────────────────────────────────────────────────

def revoke_employee_account(
    db: Session, current: CurrentUser, employee_id: int
) -> dict:
    """
    Delete the dashboard User account linked to an employee, cascading their
    UserBranchRole rows. The Employee, profile picture, schedule and history
    all survive (`User.employee_id` is the only thing destroyed; the
    relationship is `ondelete=SET NULL` from the other direction).

    Permission: `manage_users` — owner-only by default. Refuses to revoke
    an owner account.
    """
    current.require(rbac.P_MANAGE_USERS)

    emp, _agents = _load_employee_in_tenant(db, employee_id, current.business_id)
    if emp.user is None:
        raise NotFoundException(
            detail="This employee has no dashboard account",
            code="NO_DASHBOARD_ACCOUNT",
        )
    user = emp.user
    if user.is_owner:
        raise ForbiddenException(
            detail="The owner account cannot be revoked from an employee card",
            code="OWNER_PROTECTED",
        )

    db.delete(user)
    db.commit()

    logger.info(
        f"Dashboard account revoked for employee={employee_id} "
        f"(user={user.id}) by user={current.id}"
    )
    return {"message": "Dashboard access revoked"}


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

    invite_agent_ids: list[int] = list(invite.agent_ids or [])
    if not invite_agent_ids:
        raise NotFoundException(
            detail="Invitation has no branches assigned",
            code="INVITE_NOT_FOUND",
        )

    # Use the first branch to anchor the new user's tenant; every other
    # selected branch becomes an additional UserBranchRole row.
    agents = (
        db.query(AIAgent).filter(AIAgent.id.in_(invite_agent_ids)).all()
    )
    if not agents:
        raise NotFoundException(detail="Branches no longer exist", code="AGENT_NOT_FOUND")
    business_id = agents[0].business_id

    user = User(
        business_id=business_id,
        employee_id=emp.id,
        email=invite.email,
        password_hash=hash_password(password),
        is_owner=False,
        active=True,
    )
    db.add(user)
    db.flush()

    granted_agent_ids: set[int] = set()
    for aid in invite_agent_ids:
        if aid in granted_agent_ids:
            continue
        granted_agent_ids.add(aid)
        db.add(UserBranchRole(user_id=user.id, agent_id=aid, role_id=invite.role_id))

    invite.status = InviteStatusEnum.accepted

    tokens = issue_tokens(db, user)
    db.commit()

    logger.info(
        f"Dashboard account created for employee={emp.id} "
        f"(user={user.id}, email={invite.email}, branches={sorted(granted_agent_ids)})"
    )
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
