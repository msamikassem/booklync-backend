"""
app/api/v1/endpoints/staff_auth.py

Staff authentication and staff-scoped appointment routes.

Public endpoints: /login, /refresh, /accept-invite
Protected endpoints (staff JWT): /logout, /me, /appointments, /appointments/{id}/status
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_staff_account, get_db
from app.models.models import AppointmentStatusEnum, StaffAccount
from app.schemas.appointment import AppointmentListResponse, AppointmentDetailResponse
from app.schemas.auth import MessageResponse
from app.schemas.availability import AgentTimeResponse
from app.schemas.business_hours import AgentDaysOffResponse, BusinessHoursResponse
from app.schemas.employee import BlockedTimesResponse, WorkingHoursResponse
from app.schemas.staff_auth import (
    AcceptInviteRequest,
    StaffAccessTokenResponse,
    StaffAppointmentStatusUpdate,
    StaffLoginRequest,
    StaffMeResponse,
    StaffRefreshRequest,
    StaffTokenResponse,
)
from app.services import staff_auth_service

router = APIRouter()


# ── Public ────────────────────────────────────────────────────────────────────

@router.post("/accept-invite", response_model=StaffTokenResponse, status_code=201)
def accept_invite(data: AcceptInviteRequest, db: Session = Depends(get_db)):
    """Accept a dashboard invite and create a staff account. Returns tokens (logs you in)."""
    return staff_auth_service.accept_invite(db, data.token, data.password)


@router.post("/login", response_model=StaffTokenResponse)
def staff_login(data: StaffLoginRequest, db: Session = Depends(get_db)):
    """Log in with staff email and password."""
    return staff_auth_service.staff_login(db, data.email, data.password)


@router.post("/refresh", response_model=StaffAccessTokenResponse)
def staff_refresh(data: StaffRefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid staff refresh token for a new access token."""
    return staff_auth_service.refresh_staff_token(db, data.refresh_token)


# ── Authenticated (staff JWT required) ───────────────────────────────────────

@router.post("/logout", response_model=MessageResponse)
def staff_logout(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Revoke the staff refresh token."""
    return staff_auth_service.staff_logout(db, staff.id)


@router.get("/me", response_model=StaffMeResponse)
def staff_me(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Return profile of the currently authenticated staff member."""
    return staff_auth_service.get_staff_me(db, staff.id)


# ── Calendar context (read-only, own branch + own schedule) ──────────────────

@router.get("/time", response_model=AgentTimeResponse)
def staff_agent_time(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Current wall-clock time for the staff member's branch (anchors the calendar 'now')."""
    return staff_auth_service.get_staff_agent_time(db, staff)


@router.get("/business-hours", response_model=BusinessHoursResponse)
def staff_business_hours(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Weekly opening schedule for the staff member's branch."""
    return staff_auth_service.get_staff_business_hours(db, staff)


@router.get("/working-hours", response_model=WorkingHoursResponse)
def staff_working_hours(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """The staff member's own weekly working hours."""
    return staff_auth_service.get_staff_working_hours(db, staff)


@router.get("/blocked-times", response_model=BlockedTimesResponse)
def staff_blocked_times(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """The staff member's own recurring breaks."""
    return staff_auth_service.get_staff_blocked_times(db, staff)


@router.get("/days-off", response_model=AgentDaysOffResponse)
def staff_days_off(
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Branch-wide closures plus the staff member's own personal days off."""
    return staff_auth_service.get_staff_days_off(db, staff)


@router.get("/appointments", response_model=AppointmentListResponse)
def staff_appointments(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    status: Optional[AppointmentStatusEnum] = Query(None),
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """Return appointments assigned to the authenticated staff member only."""
    return staff_auth_service.get_staff_appointments(
        db,
        staff=staff,
        date_from=date_from,
        date_to=date_to,
        status=status.value if status else None,
    )


@router.patch("/appointments/{appointment_id}/status", response_model=AppointmentDetailResponse)
def staff_update_appointment_status(
    appointment_id: int,
    data: StaffAppointmentStatusUpdate,
    db: Session = Depends(get_db),
    staff: StaffAccount = Depends(get_current_staff_account),
):
    """
    Update the status of one of your own appointments.
    Allowed values: completed, no_show, cancelled.
    """
    data.validate_allowed()
    return staff_auth_service.update_staff_appointment_status(
        db,
        appointment_id=appointment_id,
        status=AppointmentStatusEnum(data.status),
        staff=staff,
    )
