"""
app/schemas/employee.py

Pydantic schemas for employee management.

Employees belong to an AI agent (branch).
Each employee has:
  - working hours (regular weekly schedule)
  - blocked times (specific date/time unavailability)
  - personal days off
  - services they can perform
"""

from pydantic import BaseModel, field_validator, model_validator
from typing import Literal, Optional
from datetime import datetime, date, time


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    """
    Data required to create a new employee.
    Used by: POST /api/v1/employees/

    `agent_ids` is the list of branches the employee should be assigned to
    (must contain at least one branch, all in the caller's business). The
    `can_receive_bookings` flag is applied uniformly to every selected branch
    at creation time; per-branch overrides happen later via the activate /
    deactivate / per-branch endpoints.
    """
    agent_ids: list[int]
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    can_receive_bookings: bool = True

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Employee name cannot be empty")
        return value.strip()

    @field_validator("agent_ids")
    @classmethod
    def agents_not_empty_and_unique(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("agent_ids must include at least one branch")
        seen: set[int] = set()
        deduped: list[int] = []
        for aid in value:
            if aid in seen:
                continue
            seen.add(aid)
            deduped.append(aid)
        return deduped


class EmployeeUpdate(BaseModel):
    """
    Update an existing employee. Personal fields (name/role/phone/email)
    apply to the Employee row; `can_receive_bookings` is per-branch and
    updates the EmployeeBranch row for the agent_id sent on the query string.
    """
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    can_receive_bookings: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Employee name cannot be empty")
        return value.strip() if value else value


class AssignBranchRequest(BaseModel):
    """Add an existing employee to an additional branch."""
    agent_id: int
    can_receive_bookings: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# WORKING HOURS REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class WorkingHourCreate(BaseModel):
    """
    A single working hour entry for one day of the week.
    day_of_week: 0=Monday, 1=Tuesday ... 6=Sunday
    """
    day_of_week: int
    start_time: time      # e.g. "09:00:00"
    end_time: time        # e.g. "17:00:00"

    @field_validator("day_of_week")
    @classmethod
    def valid_day(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        return value

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, end_time: time, info) -> time:
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return end_time


class WorkingHoursSet(BaseModel):
    """
    Set the full weekly schedule for an employee.
    Sends all days at once — replaces the existing schedule entirely.
    Used by: POST /api/v1/employees/{id}/working-hours
    """
    working_hours: list[WorkingHourCreate]

    @field_validator("working_hours")
    @classmethod
    def no_duplicate_days(cls, value: list) -> list:
        days = [wh.day_of_week for wh in value]
        if len(days) != len(set(days)):
            raise ValueError("Duplicate days found — each day of the week can only appear once")
        return value


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED TIME REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class BlockedTimeCreate(BaseModel):
    """
    A recurring weekly time block when an employee is unavailable.
    Used for regular breaks like lunch or recurring meetings.
    day_of_week: 0=Monday ... 6=Sunday
    """
    day_of_week: int
    start_time: time
    end_time: time
    reason: Optional[str] = None

    @field_validator("day_of_week")
    @classmethod
    def valid_day(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        return value

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, end_time: time, info) -> time:
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return end_time



# ─────────────────────────────────────────────────────────────────────────────
# DAY OFF REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class DayOffCreate(BaseModel):
    """
    A personal day off for an employee.
    Used by: POST /api/v1/employees/{id}/days-off
    """
    name: Optional[str] = None        # e.g. "Vacation", "Sick leave"
    start_date: date
    end_date: date

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, end_date: date, info) -> date:
        start_date = info.data.get("start_date")
        if start_date and end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        return end_date


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas
# ─────────────────────────────────────────────────────────────────────────────

class DashboardAccessBranch(BaseModel):
    """One branch the active dashboard user has access at."""
    agent_id: int
    agent_name: str
    role_key: str
    role_name: str


class DashboardAccessSummary(BaseModel):
    """
    Current state of an employee's dashboard-login access. Always present on
    EmployeeResponse so the dashboard can render the status pill + decide
    which actions to show.

    `status` values:
      • `none`              — no User account, no live invite. The owner sees
                              a 'Send invite' affordance.
      • `invite_pending`    — UserInvite with status='pending' and not past
                              expires_at. Owner can resend, edit, or cancel.
      • `invite_expired`    — UserInvite with status='expired' OR status='pending'
                              but past expires_at. Owner can resend (mints a
                              fresh token) or cancel.
      • `active`            — User row exists for this employee. Owner can edit
                              the user's branch assignments or revoke entirely.
    """
    status: Literal["none", "invite_pending", "invite_expired", "active"]
    # Active account fields (status == "active")
    user_id: Optional[int] = None
    account_branches: Optional[list[DashboardAccessBranch]] = None
    # Invite fields (status in {invite_pending, invite_expired})
    invite_id: Optional[int] = None
    invite_role: Optional[str] = None
    invite_agent_ids: Optional[list[int]] = None
    invite_expires_at: Optional[datetime] = None
    # Shared (always set when we have an email on file for the access path)
    email: Optional[str] = None


class EmployeeBranchSummary(BaseModel):
    """One entry in EmployeeResponse.branches — per-branch flags + agent name."""
    agent_id: int
    agent_name: str
    active: bool
    can_receive_bookings: bool

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, value):
        # Accept an EmployeeBranch ORM object directly: pull agent_name from
        # the joined AIAgent relation, since it isn't a column on the join row.
        if isinstance(value, dict):
            return value
        agent = getattr(value, "agent", None)
        return {
            "agent_id": getattr(value, "agent_id", None),
            "agent_name": getattr(agent, "agent_name", "") if agent is not None else "",
            "active": getattr(value, "active", True),
            "can_receive_bookings": getattr(value, "can_receive_bookings", True),
        }


class EmployeeResponse(BaseModel):
    id: int
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    profile_picture_url: Optional[str] = None
    created_at: datetime
    # Every branch the employee is currently assigned to, with that branch's
    # active + can_receive_bookings flags. Always present and non-empty.
    branches: list[EmployeeBranchSummary]
    # Current state of the employee's dashboard-login access (always present;
    # status='none' when there's no user/invite). Lets the dashboard render
    # the status pill + the right management actions without a follow-up call.
    dashboard_access: DashboardAccessSummary

    model_config = {"from_attributes": True}


class EmployeeListResponse(BaseModel):
    employees: list[EmployeeResponse]
    total: int


class WorkingHourResponse(BaseModel):
    id: int
    employee_id: int
    day_of_week: int
    start_time: time
    end_time: time

    model_config = {"from_attributes": True}


class WorkingHoursResponse(BaseModel):
    working_hours: list[WorkingHourResponse]
    total: int


class BlockedTimeResponse(BaseModel):
    id: int
    employee_id: int
    day_of_week: int
    start_time: time
    end_time: time
    reason: Optional[str] = None

    model_config = {"from_attributes": True}


class BlockedTimesResponse(BaseModel):
    blocked_times: list[BlockedTimeResponse]
    total: int


class DayOffResponse(BaseModel):
    id: int
    employee_id: Optional[int] = None
    agent_id: int
    name: Optional[str] = None
    start_date: date
    end_date: date

    model_config = {"from_attributes": True}


class DaysOffResponse(BaseModel):
    days_off: list[DayOffResponse]
    total: int


class MessageResponse(BaseModel):
    message: str