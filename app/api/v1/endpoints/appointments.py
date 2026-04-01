"""
app/api/v1/endpoints/appointments.py

Appointment management routes.
All endpoints are protected — require a valid JWT token.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentStatusUpdate,
    AppointmentDetailResponse,
    AppointmentListResponse,
    MessageResponse,
)
from app.services import appointment_service

router = APIRouter()


@router.post(
    "/",
    response_model=AppointmentDetailResponse,
    status_code=201,
    summary="Create a new appointment",
)
def create_appointment(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Create a new appointment.

    end_time is calculated automatically from the service duration.
    Checks for overlapping appointments before creating.
    Cannot create appointments in the past.
    """
    return appointment_service.create_appointment(db, data, current_business.id)


@router.get(
    "/",
    response_model=AppointmentListResponse,
    status_code=200,
    summary="Get appointments with optional filters",
)
def get_appointments(
    agent_id: int = Query(..., description="Which agent"),
    date_from: Optional[datetime] = Query(None, description="Start of date range"),
    date_to: Optional[datetime] = Query(None, description="End of date range"),
    employee_id: Optional[int] = Query(None, description="Filter by employee"),
    customer_id: Optional[int] = Query(None, description="Filter by customer"),
    status: Optional[str] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns appointments for an agent with optional filters.
    Use date_from and date_to to get appointments for a specific range.
    """
    return appointment_service.get_appointments(
        db=db,
        agent_id=agent_id,
        business_id=current_business.id,
        date_from=date_from,
        date_to=date_to,
        employee_id=employee_id,
        customer_id=customer_id,
        status=status,
    )


@router.get(
    "/{appointment_id}",
    response_model=AppointmentDetailResponse,
    status_code=200,
    summary="Get one appointment",
)
def get_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns one appointment by id with full details."""
    return appointment_service.get_appointment(db, appointment_id, current_business.id)


@router.put(
    "/{appointment_id}",
    response_model=AppointmentDetailResponse,
    status_code=200,
    summary="Update an appointment",
)
def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Update an appointment — reschedule or change employee.
    Cannot update cancelled, completed, or no_show appointments.
    """
    return appointment_service.update_appointment(
        db, appointment_id, data, current_business.id
    )


@router.patch(
    "/{appointment_id}/status",
    response_model=AppointmentDetailResponse,
    status_code=200,
    summary="Update appointment status",
)
def update_appointment_status(
    appointment_id: int,
    data: AppointmentStatusUpdate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Update the status of an appointment.
    Valid statuses: pending, confirmed, cancelled, completed, no_show
    """
    return appointment_service.update_appointment_status(
        db, appointment_id, data.status, current_business.id
    )


@router.patch(
    "/{appointment_id}/cancel",
    response_model=AppointmentDetailResponse,
    status_code=200,
    summary="Cancel an appointment",
)
def cancel_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Cancel an appointment.
    Shortcut for PATCH /status with status=cancelled.
    """
    return appointment_service.cancel_appointment(
        db, appointment_id, current_business.id
    )