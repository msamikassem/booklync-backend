"""
app/schemas/user.py

Pydantic schemas for dashboard user management, invitations, roles and
permissions (the RBAC admin surface under /api/v1/users and /api/v1/roles).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ── Invite ──────────────────────────────────────────────────────────────────

class InviteUserRequest(BaseModel):
    """
    Body for POST /employees/{id}/invite.
    `role` is a role key (branch_manager | receptionist | staff) applied at
    every selected branch. `agent_ids` is optional — when omitted, the invite
    covers every branch the employee is currently assigned to. When provided
    it must be a non-empty subset of those branches. Email is optional if the
    employee already has one on file.
    """
    role: str
    email: Optional[EmailStr] = None
    agent_ids: Optional[list[int]] = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)


class InviteResponse(BaseModel):
    message: str
    email: str
    role: str
    expires_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ── User listing / management ─────────────────────────────────────────────────

class BranchRoleInfo(BaseModel):
    agent_id: int
    agent_name: str
    role_key: str
    role_name: str


class UserSummary(BaseModel):
    id: int
    email: str
    is_owner: bool
    active: bool
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    branches: list[BranchRoleInfo]


class UserListResponse(BaseModel):
    users: list[UserSummary]
    total: int


class AssignBranchRoleRequest(BaseModel):
    """Grant an existing user a role at an additional branch (multi-branch support)."""
    agent_id: int
    role: str


class MessageResponse(BaseModel):
    message: str


# ── Roles / permissions catalogue ─────────────────────────────────────────────

class RoleInfo(BaseModel):
    id: int
    key: str
    name: str
    description: Optional[str] = None
    is_system: bool
    permissions: list[str]


class RoleListResponse(BaseModel):
    roles: list[RoleInfo]


class PermissionInfo(BaseModel):
    key: str
    description: str
    category: Optional[str] = None


class PermissionListResponse(BaseModel):
    permissions: list[PermissionInfo]
