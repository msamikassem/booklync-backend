"""
app/api/v1/endpoints/users.py

RBAC admin routes — dashboard user management, invitations, and the
roles / permissions catalogue.

Public:        POST /accept-invite
Authenticated: GET / · PATCH /{id}/activate·/deactivate ·
               POST /{id}/branch-roles · DELETE /{id}/branch-roles/{agent_id} ·
               GET /roles · GET /permissions
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.user import (
    AcceptInviteRequest,
    AssignBranchRoleRequest,
    MessageResponse,
    PermissionListResponse,
    RoleListResponse,
    TokenResponse,
    UserListResponse,
    UserSummary,
)
from app.services import user_service

router = APIRouter()


# ── Public ────────────────────────────────────────────────────────────────────

@router.post("/accept-invite", response_model=TokenResponse, status_code=201)
def accept_invite(data: AcceptInviteRequest, db: Session = Depends(get_db)):
    """Accept a dashboard invite, create the account, and log in (returns tokens)."""
    return user_service.accept_invite(db, data.token, data.password)


# ── Roles & permissions catalogue ─────────────────────────────────────────────

@router.get("/roles", response_model=RoleListResponse)
def list_roles(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """System role presets + this tenant's custom roles (for the role picker)."""
    return user_service.list_roles(db, current)


@router.get("/permissions", response_model=PermissionListResponse)
def list_permissions(
    current: CurrentUser = Depends(get_current_user),
):
    """The full permission catalogue (for future custom-role configuration)."""
    return user_service.list_permissions()


# ── User management (requires manage_users) ───────────────────────────────────

@router.get("/", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """List all dashboard users in the tenant with their branch roles."""
    return user_service.list_users(db, current)


@router.patch("/{user_id}/activate", response_model=UserSummary)
def activate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    return user_service.set_user_active(db, current, user_id, True)


@router.patch("/{user_id}/deactivate", response_model=UserSummary)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    return user_service.set_user_active(db, current, user_id, False)


@router.post("/{user_id}/branch-roles", response_model=UserSummary)
def assign_branch_role(
    user_id: int,
    data: AssignBranchRoleRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Grant a user a role at an additional branch (multi-branch assignment)."""
    return user_service.assign_branch_role(db, current, user_id, data.agent_id, data.role)


@router.delete("/{user_id}/branch-roles/{agent_id}", response_model=MessageResponse)
def revoke_branch_role(
    user_id: int,
    agent_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    return user_service.revoke_branch_role(db, current, user_id, agent_id)
