"""
app/schemas/staff_auth.py

Pydantic schemas for staff authentication and invite flows.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.models.models import AppointmentStatusEnum


# ── Invite ────────────────────────────────────────────────────────────────────

class StaffInviteRequest(BaseModel):
    """Body for POST /employees/{id}/invite. Email is optional if the employee already has one."""
    email: Optional[EmailStr] = None


class InviteResponse(BaseModel):
    message: str
    email: str
    expires_at: datetime


# ── Accept ────────────────────────────────────────────────────────────────────

class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)


# ── Login / tokens ────────────────────────────────────────────────────────────

class StaffLoginRequest(BaseModel):
    email: EmailStr
    password: str


class StaffTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class StaffRefreshRequest(BaseModel):
    refresh_token: str


class StaffAccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Me ────────────────────────────────────────────────────────────────────────

class StaffMeResponse(BaseModel):
    id: int
    employee_id: int
    email: str
    active: bool
    employee_name: str
    agent_id: int
    agent_name: str
    business_id: int
    business_name: str


# ── Appointment status update (staff — restricted to terminal statuses) ───────

class StaffAppointmentStatusUpdate(BaseModel):
    status: AppointmentStatusEnum

    model_config = {"use_enum_values": True}

    def validate_allowed(self) -> None:
        allowed = {
            AppointmentStatusEnum.completed,
            AppointmentStatusEnum.no_show,
            AppointmentStatusEnum.cancelled,
        }
        if AppointmentStatusEnum(self.status) not in allowed:
            from app.core.exceptions import AppException
            raise AppException(
                detail="Staff may only set status to completed, no_show, or cancelled",
                code="FORBIDDEN_STATUS",
                status_code=403,
            )
