"""
app/api/v1/endpoints/employees.py

Employee management routes.

All endpoints are protected — require a valid JWT token.
Every endpoint takes agent_id as a query parameter so the
ownership chain can be verified:
    business → agent → employee
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeUpdate,
    WorkingHoursSet,
    BlockedTimeCreate,
    DayOffCreate,
    EmployeeResponse,
    EmployeeListResponse,
    WorkingHoursResponse,
    BlockedTimeResponse,
    BlockedTimesResponse,
    DayOffResponse,
    DaysOffResponse,
    MessageResponse,
)
from app.services import employee_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=EmployeeResponse,
    status_code=201,
    summary="Create a new employee",
)
def create_employee(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Create a new employee under an agent.
    The agent_id is required in the request body.
    """
    return employee_service.create_employee(db, data, current_business.id)


@router.get(
    "/",
    response_model=EmployeeListResponse,
    status_code=200,
    summary="Get all employees for an agent",
)
def get_employees(
    agent_id: int = Query(..., description="The agent to get employees for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all employees for a specific agent.
    Pass agent_id as a query parameter: /api/v1/employees/?agent_id=1
    """
    return employee_service.get_employees(db, agent_id, current_business.id)


@router.get(
    "/{employee_id}",
    response_model=EmployeeResponse,
    status_code=200,
    summary="Get one specific employee",
)
def get_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns one employee by id.
    Requires agent_id as query param to verify the ownership chain.
    """
    return employee_service.get_employee(db, employee_id, agent_id, current_business.id)


@router.put(
    "/{employee_id}",
    response_model=EmployeeResponse,
    status_code=200,
    summary="Update an employee",
)
def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Update an existing employee. All fields are optional.
    Only send the fields you want to change.
    """
    return employee_service.update_employee(db, employee_id, agent_id, data, current_business.id)


@router.delete(
    "/{employee_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete an employee",
)
def delete_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Permanently delete an employee and all their related data.
    Includes working hours, blocked times, days off, and appointments.
    """
    return employee_service.delete_employee(db, employee_id, agent_id, current_business.id)


@router.patch(
    "/{employee_id}/activate",
    response_model=EmployeeResponse,
    status_code=200,
    summary="Activate an employee",
)
def activate_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Activate an employee so they can receive bookings."""
    return employee_service.activate_employee(db, employee_id, agent_id, current_business.id)


@router.patch(
    "/{employee_id}/deactivate",
    response_model=EmployeeResponse,
    status_code=200,
    summary="Deactivate an employee",
)
def deactivate_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Deactivate an employee so they stop receiving bookings."""
    return employee_service.deactivate_employee(db, employee_id, agent_id, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# WORKING HOURS
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{employee_id}/working-hours",
    response_model=WorkingHoursResponse,
    status_code=200,
    summary="Set working hours for an employee",
)
def set_working_hours(
    employee_id: int,
    data: WorkingHoursSet,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Set the weekly working schedule for an employee.
    This REPLACES the existing schedule entirely.
    Send all days you want — any days not included are removed.
    """
    return employee_service.set_working_hours(db, employee_id, agent_id, data, current_business.id)


@router.get(
    "/{employee_id}/working-hours",
    response_model=WorkingHoursResponse,
    status_code=200,
    summary="Get working hours for an employee",
)
def get_working_hours(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns the weekly working schedule for an employee."""
    return employee_service.get_working_hours(db, employee_id, agent_id, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED TIMES
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{employee_id}/blocked-times",
    response_model=BlockedTimeResponse,
    status_code=201,
    summary="Add a blocked time for an employee",
)
def add_blocked_time(
    employee_id: int,
    data: BlockedTimeCreate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Add a specific date/time block when the employee is unavailable.
    Used for one-off blocks like lunch breaks or personal appointments.
    """
    return employee_service.add_blocked_time(db, employee_id, agent_id, data, current_business.id)


@router.get(
    "/{employee_id}/blocked-times",
    response_model=BlockedTimesResponse,
    status_code=200,
    summary="Get all blocked times for an employee",
)
def get_blocked_times(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns all blocked times for an employee ordered by date."""
    return employee_service.get_blocked_times(db, employee_id, agent_id, current_business.id)


@router.delete(
    "/{employee_id}/blocked-times/{blocked_time_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Remove a blocked time",
)
def delete_blocked_time(
    employee_id: int,
    blocked_time_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Remove a specific blocked time entry."""
    return employee_service.delete_blocked_time(
        db, employee_id, agent_id, blocked_time_id, current_business.id
    )


# ─────────────────────────────────────────────────────────────────────────────
# PERSONAL DAYS OFF
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{employee_id}/days-off",
    response_model=DayOffResponse,
    status_code=201,
    summary="Add a personal day off for an employee",
)
def add_day_off(
    employee_id: int,
    data: DayOffCreate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Add a personal day off for an employee.
    For branch-wide closures use the business hours endpoints instead.
    """
    return employee_service.add_day_off(db, employee_id, agent_id, data, current_business.id)


@router.get(
    "/{employee_id}/days-off",
    response_model=DaysOffResponse,
    status_code=200,
    summary="Get all personal days off for an employee",
)
def get_days_off(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns all personal days off for an employee ordered by date."""
    return employee_service.get_days_off(db, employee_id, agent_id, current_business.id)


@router.delete(
    "/{employee_id}/days-off/{day_off_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Remove a personal day off",
)
def delete_day_off(
    employee_id: int,
    day_off_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Remove a specific personal day off entry."""
    return employee_service.delete_day_off(
        db, employee_id, agent_id, day_off_id, current_business.id
    )