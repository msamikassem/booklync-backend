"""
app/api/v1/endpoints/availability.py

Four availability endpoints:

    1. GET /api/v1/availability/date   — all slots on a date (authenticated)
    2. GET /api/v1/availability/check  — check specific time, closest if unavailable
    3. GET /api/v1/availability/public/date — all slots on a date (public)
    4. GET /api/v1/availability/time   — current agent local time (authenticated)
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.availability import (
    AvailabilityResponse,
    SlotCheckResponse,
    AgentTimeResponse,
)
from app.services import availability_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 — all slots on a date (authenticated)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/date",
    response_model=AvailabilityResponse,
    status_code=200,
    summary="Get all available slots on a date",
)
def get_availability(
    agent_id: int = Query(..., description="Which branch to check"),
    service_id: int = Query(..., description="Which service (determines duration)"),
    date: date = Query(..., description="Date to check — YYYY-MM-DD"),
    employee_id: Optional[int] = Query(None, description="Optional — filter to specific employee"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all available booking slots for a given date and service.

    Excludes:
    - Slots in the past
    - Slots within the minimum advance booking time (default 60 min)
    - Slots on days the branch is closed
    - Slots during branch-wide days off
    - Slots where the employee is on personal leave
    - Slots that overlap existing appointments
    - Slots that overlap recurring blocked times
    """
    return availability_service.get_availability(
        db=db,
        agent_id=agent_id,
        service_id=service_id,
        requested_date=date,
        employee_id=employee_id,
        business_id=current_business.id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — check specific time
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/check",
    response_model=SlotCheckResponse,
    status_code=200,
    summary="Check if a specific time is available",
)
def check_slot(
    agent_id: int = Query(..., description="Which branch"),
    service_id: int = Query(..., description="Which service"),
    start_time: datetime = Query(..., description="Requested start time — ISO 8601 with timezone e.g. 2025-06-16T10:00:00+00:00"),
    employee_id: Optional[int] = Query(None, description="Optional — specific employee"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Check if a specific date and time is available for booking.

    If available:
        Returns available=true and the slot details.

    If not available:
        Returns available=false and the closest available slots
        on each side of the requested time (default 3 before, 3 after).
    """
    return availability_service.check_slot(
        db=db,
        agent_id=agent_id,
        service_id=service_id,
        requested_start=start_time,
        employee_id=employee_id,
        business_id=current_business.id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — all slots on a date (public)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/public/date",
    response_model=AvailabilityResponse,
    status_code=200,
    summary="Get all available slots — public (no auth required)",
)
def get_public_availability(
    agent_id: int = Query(..., description="Which branch"),
    service_id: int = Query(..., description="Which service"),
    date: date = Query(..., description="Date to check — YYYY-MM-DD"),
    employee_id: Optional[int] = Query(None, description="Optional — specific employee"),
    db: Session = Depends(get_db),
):
    """
    Public availability check — no authentication required.

    Used by the customer-facing booking page.
    Only works for active agents and active services.
    Same slot logic as the authenticated endpoint.
    """
    return availability_service.get_public_availability(
        db=db,
        agent_id=agent_id,
        service_id=service_id,
        requested_date=date,
        employee_id=employee_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 4 — current agent local time
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/time",
    response_model=AgentTimeResponse,
    status_code=200,
    summary="Get current local time for an agent",
)
def get_agent_time(
    agent_id: int = Query(..., description="Which agent to get time for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns the current local time for an agent based on their timezone.

    Also returns:
    - Whether the branch is currently open
    - Today's opening and closing times
    - UTC time for reference

    Useful for the AI agent to know the current local time before
    making or checking bookings.
    """
    return availability_service.get_agent_time(
        db=db,
        agent_id=agent_id,
        business_id=current_business.id,
    )