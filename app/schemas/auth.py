"""
app/schemas/auth.py

Pydantic schemas for authentication.

These define the shape of data:
  - Coming IN  → request bodies (what the caller must send)
  - Going OUT  → response bodies (what we send back)

Pydantic validates automatically:
  - wrong types      → 422 error
  - missing fields   → 422 error
  - invalid email    → 422 error
  - password too short → 422 error
"""

from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST schemas — data coming IN from the caller
# ─────────────────────────────────────────────────────────────────────────────

class BusinessRegister(BaseModel):
    """
    Data required to register a new business account.
    Used by: POST /api/v1/auth/register
    """
    name: str
    email: EmailStr          # EmailStr validates it's a real email format
    password: str
    phone: str
    business_type_id: int

    @field_validator("password")
    @classmethod
    def password_min_length(cls, value: str) -> str:
        """Password must be at least 8 characters."""
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters")
        return value

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, value: str) -> str:
        """Phone number cannot be empty or just whitespace."""
        if not value.strip():
            raise ValueError("Phone number cannot be empty")
        return value.strip()

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        """Business name cannot be empty or just whitespace."""
        if not value.strip():
            raise ValueError("Business name cannot be empty")
        return value.strip()


class BusinessLogin(BaseModel):
    """
    Data required to log in.
    Used by: POST /api/v1/auth/login
    """
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    """
    Data required to get a new access token.
    Used by: POST /api/v1/auth/refresh
    """
    refresh_token: str


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas — data going OUT to the caller
# ─────────────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """
    Returned after successful login or register.
    Contains both tokens the client needs to store.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"    # always "bearer" — standard JWT convention


class AccessTokenResponse(BaseModel):
    """
    Returned after a successful token refresh.
    Only contains a new access token — refresh token stays the same.
    """
    access_token: str
    token_type: str = "bearer"


class BusinessResponse(BaseModel):
    """
    Business data returned to the client.
    Used by: GET /api/v1/auth/me

    Notice: password_hash and refresh_token are NOT here.
    You never send sensitive fields back to the client.
    """
    id: int
    name: str
    email: str
    phone: str
    business_type_id: int
    subscription_plan: str
    active: bool
    # This tells Pydantic to read data from SQLAlchemy model attributes
    # Without this, Pydantic wouldn't know how to read SQLAlchemy objects
    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """
    Simple message response for actions that don't return data.
    Used by: POST /api/v1/auth/logout
    """
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# RBAC identity — what GET /auth/me returns
# ─────────────────────────────────────────────────────────────────────────────

class BranchAccess(BaseModel):
    """One branch the user can access, with the role + permissions they hold there."""
    agent_id: int
    agent_name: str
    role_key: str
    role_name: str
    permissions: list[str]


class UserMeResponse(BaseModel):
    """
    Identity + access profile of the authenticated user.
    The dashboard reads this once after login to render role-aware UI.
    """
    id: int
    email: str
    is_owner: bool
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    business_id: int
    business_name: str
    subscription_plan: str
    # Aggregated permission keys across all branches (owner → every key).
    permissions: list[str]
    branches: list[BranchAccess]