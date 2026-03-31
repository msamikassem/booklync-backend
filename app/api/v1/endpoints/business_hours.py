"""
app/api/v1/endpoints/business_hours.py

Business hours and agent days off routes.

All endpoints are protected — require a valid JWT token.
agent_id is passed either in the request body or as a query parameter.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.business_hours import (
    BusinessHoursSet,
    AgentDayOffCreate,
    BusinessHoursResponse,
    AgentDayOffResponse,
    AgentDaysOffResponse,
    MessageResponse,
)
from app.services import business_hours_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS HOURS
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/business-hours/",
    response_model=BusinessHoursResponse,
    status_code=200,
    summary="Set business hours for an agent",
)
def set_business_hours(
    data: BusinessHoursSet,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Set the weekly opening schedule for an agent.
    This REPLACES the existing schedule entirely.
    Days not included are treated as closed.
    agent_id is included in the request body.
    """
    return business_hours_service.set_business_hours(db, data, current_business.id)


@router.get(
    "/business-hours/",
    response_model=BusinessHoursResponse,
    status_code=200,
    summary="Get business hours for an agent",
)
def get_business_hours(
    agent_id: int = Query(..., description="The agent to get business hours for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns the weekly opening schedule for an agent.
    Results ordered Monday to Sunday.
    Days with no entry are closed.
    """
    return business_hours_service.get_business_hours(db, agent_id, current_business.id)


@router.delete(
    "/business-hours/{business_hour_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a specific day's business hours",
)
def delete_business_hour(
    business_hour_id: int,
    agent_id: int = Query(..., description="The agent this business hour belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Remove a specific day's opening hours.
    That day will be treated as closed after deletion.
    """
    return business_hours_service.delete_business_hour(
        db, business_hour_id, agent_id, current_business.id
    )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT DAYS OFF
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/days-off/",
    response_model=AgentDayOffResponse,
    status_code=201,
    summary="Add a branch-wide day off",
)
def add_agent_day_off(
    data: AgentDayOffCreate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Add a branch-wide closure period.
    Applies to ALL employees — no bookings possible during this period.
    Use this for public holidays, renovations, special closures.
    agent_id is included in the request body.
    """
    return business_hours_service.add_agent_day_off(db, data, current_business.id)


@router.get(
    "/days-off/",
    response_model=AgentDaysOffResponse,
    status_code=200,
    summary="Get all branch-wide days off",
)
def get_agent_days_off(
    agent_id: int = Query(..., description="The agent to get days off for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all branch-wide closure periods for an agent.
    Personal employee days off are not included here.
    """
    return business_hours_service.get_agent_days_off(db, agent_id, current_business.id)


@router.delete(
    "/days-off/{day_off_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Remove a branch-wide day off",
)
def delete_agent_day_off(
    day_off_id: int,
    agent_id: int = Query(..., description="The agent this day off belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Remove a specific branch-wide closure period."""
    return business_hours_service.delete_agent_day_off(
        db, day_off_id, agent_id, current_business.id
    )