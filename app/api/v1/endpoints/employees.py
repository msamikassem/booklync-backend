"""
app/api/v1/endpoints/employees.py

Employee management routes.

All endpoints are authenticated. Access is RBAC-scoped per branch:
  • reads (list/get, capabilities, schedule reads) → caller must have access to
    the branch (`require_branch`)
  • employee writes (create/update/delete/activate, profile pic, capabilities)
    → `manage_employees` at the branch
  • schedule writes (working hours, blocked times, days off)
    → `manage_schedules` at the branch
  • invite → `invite_users` (enforced inside user_service)
"""

from decimal import Decimal as _Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
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
from app.schemas.user import InviteUserRequest, InviteResponse
from app.services import employee_service
from app.services import storage_service
from app.services import user_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/", response_model=EmployeeResponse, status_code=201, summary="Create a new employee")
def create_employee(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Create a new employee under a branch. The agent_id is required in the body."""
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=data.agent_id)
    return employee_service.create_employee(db, data, current.business_id)


@router.get("/", response_model=EmployeeListResponse, summary="Get all employees for an agent")
def get_employees(
    agent_id: int = Query(..., description="The agent to get employees for"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Returns all employees for a specific branch."""
    current.require_branch(agent_id)
    return employee_service.get_employees(db, agent_id, current.business_id)


@router.get("/{employee_id}", response_model=EmployeeResponse, summary="Get one specific employee")
def get_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require_branch(agent_id)
    return employee_service.get_employee(db, employee_id, agent_id, current.business_id)


@router.put("/{employee_id}", response_model=EmployeeResponse, summary="Update an employee")
def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.update_employee(db, employee_id, agent_id, data, current.business_id)


@router.delete("/{employee_id}", response_model=MessageResponse, summary="Delete an employee")
def delete_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Permanently delete an employee and all related schedule data."""
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.delete_employee(db, employee_id, agent_id, current.business_id)


@router.patch("/{employee_id}/activate", response_model=EmployeeResponse, summary="Activate an employee")
def activate_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.activate_employee(db, employee_id, agent_id, current.business_id)


@router.patch("/{employee_id}/deactivate", response_model=EmployeeResponse, summary="Deactivate an employee")
def deactivate_employee(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.deactivate_employee(db, employee_id, agent_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE PICTURE
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{employee_id}/profile-picture", response_model=EmployeeResponse, summary="Upload a profile picture")
async def upload_profile_picture(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Upload a profile picture for an employee. Max size: 2MB."""
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    content = await file.read()

    employee = employee_service.get_employee(db, employee_id, agent_id, current.business_id)
    old_url = employee.profile_picture_url

    new_url = storage_service.upload_profile_picture(content, file.filename or "", employee_id)

    if old_url:
        storage_service.delete_profile_picture(old_url)

    return employee_service.set_profile_picture(db, employee_id, agent_id, current.business_id, new_url)


@router.delete("/{employee_id}/profile-picture", response_model=MessageResponse, summary="Remove the profile picture")
def delete_profile_picture(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    employee = employee_service.get_employee(db, employee_id, agent_id, current.business_id)

    if employee.profile_picture_url:
        storage_service.delete_profile_picture(employee.profile_picture_url)
        employee_service.set_profile_picture(db, employee_id, agent_id, current.business_id, None)

    return {"message": "Profile picture removed"}


# ─────────────────────────────────────────────────────────────────────────────
# WORKING HOURS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{employee_id}/working-hours", response_model=WorkingHoursResponse, summary="Set working hours")
def set_working_hours(
    employee_id: int,
    data: WorkingHoursSet,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Replace the weekly working schedule for an employee."""
    current.require(rbac.P_MANAGE_SCHEDULES, agent_id=agent_id)
    return employee_service.set_working_hours(db, employee_id, agent_id, data, current.business_id)


@router.get("/{employee_id}/working-hours", response_model=WorkingHoursResponse, summary="Get working hours")
def get_working_hours(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require_branch(agent_id)
    return employee_service.get_working_hours(db, employee_id, agent_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED TIMES
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{employee_id}/blocked-times", response_model=BlockedTimeResponse, status_code=201, summary="Add a blocked time")
def add_blocked_time(
    employee_id: int,
    data: BlockedTimeCreate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SCHEDULES, agent_id=agent_id)
    return employee_service.add_blocked_time(db, employee_id, agent_id, data, current.business_id)


@router.get("/{employee_id}/blocked-times", response_model=BlockedTimesResponse, summary="Get all blocked times")
def get_blocked_times(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require_branch(agent_id)
    return employee_service.get_blocked_times(db, employee_id, agent_id, current.business_id)


@router.delete("/{employee_id}/blocked-times/{blocked_time_id}", response_model=MessageResponse, summary="Remove a blocked time")
def delete_blocked_time(
    employee_id: int,
    blocked_time_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SCHEDULES, agent_id=agent_id)
    return employee_service.delete_blocked_time(db, employee_id, agent_id, blocked_time_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# PERSONAL DAYS OFF
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{employee_id}/days-off", response_model=DayOffResponse, status_code=201, summary="Add a personal day off")
def add_day_off(
    employee_id: int,
    data: DayOffCreate,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SCHEDULES, agent_id=agent_id)
    return employee_service.add_day_off(db, employee_id, agent_id, data, current.business_id)


@router.get("/{employee_id}/days-off", response_model=DaysOffResponse, summary="Get all personal days off")
def get_days_off(
    employee_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require_branch(agent_id)
    return employee_service.get_days_off(db, employee_id, agent_id, current.business_id)


@router.delete("/{employee_id}/days-off/{day_off_id}", response_model=MessageResponse, summary="Remove a personal day off")
def delete_day_off(
    employee_id: int,
    day_off_id: int,
    agent_id: int = Query(..., description="The agent this employee belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SCHEDULES, agent_id=agent_id)
    return employee_service.delete_day_off(db, employee_id, agent_id, day_off_id, current.business_id)


# ─── Employee Capabilities ──────────────────────────────────────────────────

class CapabilityCreate(BaseModel):
    title_id: Optional[int] = None
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    extra_price: _Decimal = _Decimal("0")


class CapabilityResponse(BaseModel):
    id: int
    employee_id: int
    title_id: Optional[int] = None
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    extra_price: _Decimal = _Decimal("0")

    model_config = {"from_attributes": True}


@router.get("/{employee_id}/capabilities", response_model=list[CapabilityResponse], summary="List capabilities")
def list_capabilities(
    employee_id: int,
    agent_id: int = Query(...),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require_branch(agent_id)
    return employee_service.get_capabilities(db, employee_id, agent_id, current.business_id)


@router.post("/{employee_id}/capabilities", response_model=CapabilityResponse, status_code=201, summary="Add a capability")
def add_capability(
    employee_id: int,
    data: CapabilityCreate,
    agent_id: int = Query(...),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.add_capability(
        db, employee_id, agent_id, current.business_id,
        title_id=data.title_id,
        service_id=data.service_id,
        variation_id=data.variation_id,
        extra_price=data.extra_price,
    )


@router.delete("/{employee_id}/capabilities/{capability_id}", response_model=MessageResponse, summary="Remove a capability")
def remove_capability(
    employee_id: int,
    capability_id: int,
    agent_id: int = Query(...),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_EMPLOYEES, agent_id=agent_id)
    return employee_service.remove_capability(db, employee_id, agent_id, capability_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD INVITE  (email + role; branch is the employee's own branch)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{employee_id}/invite", response_model=InviteResponse, summary="Invite an employee to a dashboard login")
def invite_employee(
    employee_id: int,
    data: InviteUserRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Invite an employee to create their own dashboard login with the given role
    (branch_manager | receptionist | staff). Requires `invite_users` at the
    employee's branch. Re-sends/refreshes a live pending invite if one exists.
    """
    return user_service.send_invite(
        db,
        current=current,
        employee_id=employee_id,
        role_key=data.role,
        email=data.email,
    )
