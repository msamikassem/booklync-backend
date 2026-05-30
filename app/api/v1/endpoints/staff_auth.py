"""
app/api/v1/endpoints/staff_auth.py

The "own scope" dashboard surface for employee-linked users (the staff
dashboard). Authentication and invites now live under /auth and /users — these
routes are all authenticated with the unified user token and return only the
caller's own data.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.models import AppointmentStatusEnum
from app.schemas.appointment import AppointmentListResponse, AppointmentDetailResponse
from app.schemas.availability import AgentTimeResponse
from app.schemas.business_hours import AgentDaysOffResponse, BusinessHoursResponse
from app.schemas.employee import BlockedTimesResponse, WorkingHoursResponse
from app.schemas.staff_auth import StaffAppointmentStatusUpdate, StaffMeResponse
from app.services import staff_auth_service

router = APIRouter()


@router.get("/me", response_model=StaffMeResponse)
def staff_me(
    agent_id: Optional[int] = Query(
        None,
        description="Optional branch override. Must be one of the branches in the user's accessible set; omit to use the primary (smallest id).",
    ),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Return profile of the currently authenticated employee-linked user."""
    return staff_auth_service.get_staff_me(db, current, agent_id=agent_id)


# ── Calendar context (read-only, own branch + own schedule) ──────────────────

@router.get("/time", response_model=AgentTimeResponse)
def staff_agent_time(
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Current wall-clock time for the user's branch (anchors the calendar 'now')."""
    return staff_auth_service.get_staff_agent_time(db, current, agent_id=agent_id)


@router.get("/business-hours", response_model=BusinessHoursResponse)
def staff_business_hours(
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Weekly opening schedule for the user's branch."""
    return staff_auth_service.get_staff_business_hours(db, current, agent_id=agent_id)


@router.get("/working-hours", response_model=WorkingHoursResponse)
def staff_working_hours(
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """The user's own weekly working hours."""
    return staff_auth_service.get_staff_working_hours(db, current, agent_id=agent_id)


@router.get("/blocked-times", response_model=BlockedTimesResponse)
def staff_blocked_times(
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """The user's own recurring breaks."""
    return staff_auth_service.get_staff_blocked_times(db, current, agent_id=agent_id)


@router.get("/days-off", response_model=AgentDaysOffResponse)
def staff_days_off(
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Branch-wide closures plus the user's own personal days off."""
    return staff_auth_service.get_staff_days_off(db, current, agent_id=agent_id)


@router.get("/appointments", response_model=AppointmentListResponse)
def staff_appointments(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    status: Optional[AppointmentStatusEnum] = Query(None),
    agent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Return appointments assigned to the authenticated user only."""
    return staff_auth_service.get_staff_appointments(
        db,
        current=current,
        date_from=date_from,
        date_to=date_to,
        status=status.value if status else None,
        agent_id=agent_id,
    )


@router.patch("/appointments/{appointment_id}/status", response_model=AppointmentDetailResponse)
def staff_update_appointment_status(
    appointment_id: int,
    data: StaffAppointmentStatusUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Update the status of one of your own appointments.
    Allowed values: completed, no_show, cancelled.
    """
    data.validate_allowed()
    return staff_auth_service.update_staff_appointment_status(
        db,
        current=current,
        appointment_id=appointment_id,
        status=AppointmentStatusEnum(data.status),
    )
