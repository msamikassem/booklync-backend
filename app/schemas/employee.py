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

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime, date, time


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    """
    Data required to create a new employee.
    Used by: POST /api/v1/employees/
    """
    agent_id: int
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Employee name cannot be empty")
        return value.strip()


class EmployeeUpdate(BaseModel):
    """
    Data for updating an existing employee.
    All fields optional — only send what you want to change.
    Used by: PUT /api/v1/employees/{id}
    """
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Employee name cannot be empty")
        return value.strip() if value else value


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

class EmployeeResponse(BaseModel):
    id: int
    agent_id: int
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    active: bool
    created_at: datetime

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