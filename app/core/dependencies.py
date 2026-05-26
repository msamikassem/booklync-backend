"""
app/core/dependencies.py

Reusable FastAPI dependencies — the RBAC auth gate.

Every protected request resolves to a `CurrentUser`: a lightweight principal
that carries the authenticated user's tenant, owner flag, optional employee
link, and a per-branch permission map. Endpoints enforce access with:

    current: CurrentUser = Depends(get_current_user)
    ...
    current.require("manage_services", agent_id=agent_id)

"Can user X do Y at branch Z?" resolves as:
  • owner short-circuit (is_owner) → yes on every branch in the tenant
  • else: look up X's permission set for branch Z (built from their role there)

Scope rules like "staff can only touch their OWN appointments" are NOT
permissions — they are enforced in the endpoint/service layer using
`current.employee_id`.
"""

from typing import Optional

from collections import defaultdict

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import decode_token
from app.core.exceptions import (
    BusinessInactiveException,
    ForbiddenException,
    UnauthorizedException,
)
from app.models.models import (
    AIAgent,
    Permission,
    RolePermission,
    User,
    UserBranchRole,
)

# HTTPBearer reads the Authorization: Bearer <token> header automatically
bearer_scheme = HTTPBearer()


class CurrentUser:
    """
    The authenticated principal for one request.

    Permission data is loaded eagerly (1–2 queries) so guard checks in
    endpoints are pure in-memory lookups.
    """

    def __init__(
        self,
        user_id: int,
        business_id: int,
        email: str,
        is_owner: bool,
        employee_id: Optional[int],
        branch_permissions: dict[int, set[str]],
        accessible_agent_ids: set[int],
    ):
        self.id = user_id
        self.business_id = business_id
        self.email = email
        self.is_owner = is_owner
        self.employee_id = employee_id
        # {agent_id: {permission_key, ...}} — empty for owners (they short-circuit)
        self.branch_permissions = branch_permissions
        # branches this user may touch (owner: every branch in the tenant)
        self.accessible_agent_ids = accessible_agent_ids

    # ── Permission checks ────────────────────────────────────────────────────

    def has_perm(self, permission: str, agent_id: Optional[int] = None) -> bool:
        """
        True if the user holds `permission`.

        - agent_id given → the user must hold it at that branch.
        - agent_id None  → tenant-level check: true if held at ANY branch
          (owner-only permissions like billing are therefore owner-gated,
          since no other preset grants them).
        """
        if self.is_owner:
            return True
        if agent_id is not None:
            return permission in self.branch_permissions.get(agent_id, set())
        return any(permission in perms for perms in self.branch_permissions.values())

    def require(self, permission: str, agent_id: Optional[int] = None) -> None:
        """Raise 403 unless the user holds `permission` (optionally at `agent_id`)."""
        # A branch-scoped check implicitly also asserts branch access.
        if agent_id is not None:
            self.require_branch(agent_id)
        if not self.has_perm(permission, agent_id):
            raise ForbiddenException(
                detail="You do not have permission to perform this action",
                code="PERMISSION_DENIED",
            )

    # ── Branch-scope checks ───────────────────────────────────────────────────

    def can_access_branch(self, agent_id: int) -> bool:
        return agent_id in self.accessible_agent_ids

    def require_branch(self, agent_id: int) -> None:
        """Raise 403 unless the user may access this branch (and it's in-tenant)."""
        if not self.can_access_branch(agent_id):
            raise ForbiddenException(
                detail="You do not have access to this branch",
                code="BRANCH_FORBIDDEN",
            )


def _load_principal(db: Session, user: User) -> CurrentUser:
    """Build a CurrentUser from a loaded User row."""
    if user.is_owner:
        agent_ids = {
            row[0]
            for row in db.query(AIAgent.id).filter(AIAgent.business_id == user.business_id)
        }
        return CurrentUser(
            user_id=user.id,
            business_id=user.business_id,
            email=user.email,
            is_owner=True,
            employee_id=user.employee_id,
            branch_permissions={},
            accessible_agent_ids=agent_ids,
        )

    branch_permissions: dict[int, set[str]] = defaultdict(set)

    rows = (
        db.query(UserBranchRole.agent_id, Permission.key)
        .select_from(UserBranchRole)
        .join(RolePermission, RolePermission.role_id == UserBranchRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .filter(UserBranchRole.user_id == user.id)
    )
    for agent_id, key in rows:
        branch_permissions[agent_id].add(key)

    # Ensure branches assigned with a (hypothetically) permission-less role are
    # still treated as accessible.
    for (agent_id,) in db.query(UserBranchRole.agent_id).filter(UserBranchRole.user_id == user.id):
        branch_permissions.setdefault(agent_id, set())

    return CurrentUser(
        user_id=user.id,
        business_id=user.business_id,
        email=user.email,
        is_owner=False,
        employee_id=user.employee_id,
        branch_permissions=dict(branch_permissions),
        accessible_agent_ids=set(branch_permissions.keys()),
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """
    Authenticate the request and return the CurrentUser principal.

    Raises UnauthorizedException if the token is missing/invalid/expired or the
    user no longer exists / is inactive.
    """
    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise UnauthorizedException(detail="Invalid or expired token", code="INVALID_TOKEN")

    if payload.get("type") != "access":
        raise UnauthorizedException(detail="Invalid token type", code="INVALID_TOKEN_TYPE")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException(detail="Invalid token payload", code="INVALID_TOKEN")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise UnauthorizedException(detail="User not found", code="USER_NOT_FOUND")
    if not user.active:
        raise UnauthorizedException(detail="This account is inactive", code="ACCOUNT_INACTIVE")
    # Deactivating a tenant must lock out existing tokens immediately (not just at login).
    if not user.business or not user.business.active:
        raise BusinessInactiveException()

    return _load_principal(db, user)


def require_owner(current: "CurrentUser" = Depends(get_current_user)) -> "CurrentUser":
    """Dependency for tenant-wide, owner-only endpoints (billing, branches, settings)."""
    if not current.is_owner:
        raise ForbiddenException(
            detail="Only the account owner can perform this action",
            code="OWNER_ONLY",
        )
    return current
