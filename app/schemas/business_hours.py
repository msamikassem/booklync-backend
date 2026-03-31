"""
app/schemas/business_hours.py

Pydantic schemas for business hours and agent-level days off.

Business hours — regular weekly opening times for an agent (branch).
Agent days off — branch-wide closures (public holidays, renovations etc).

These two together define when the branch is open.
The availability engine reads them to determine bookable slots.
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import date, time


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS HOURS REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class BusinessHourCreate(BaseModel):
    """
    A single opening hours entry for one day of the week.
    day_of_week: 0=Monday, 1=Tuesday ... 6=Sunday
    """
    day_of_week: int
    open_time: time       # e.g. "09:00:00"
    closed_time: time     # e.g. "18:00:00"

    @field_validator("day_of_week")
    @classmethod
    def valid_day(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        return value

    @field_validator("closed_time")
    @classmethod
    def close_after_open(cls, closed_time: time, info) -> time:
        open_time = info.data.get("open_time")
        if open_time and closed_time <= open_time:
            raise ValueError("closed_time must be after open_time")
        return closed_time


class BusinessHoursSet(BaseModel):
    """
    Set the full weekly schedule for an agent.
    Replaces the existing schedule entirely.
    Used by: POST /api/v1/business-hours/
    """
    agent_id: int
    business_hours: list[BusinessHourCreate]

    @field_validator("business_hours")
    @classmethod
    def no_duplicate_days(cls, value: list) -> list:
        days = [bh.day_of_week for bh in value]
        if len(days) != len(set(days)):
            raise ValueError("Duplicate days found — each day can only appear once")
        return value


# ─────────────────────────────────────────────────────────────────────────────
# AGENT DAYS OFF REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class AgentDayOffCreate(BaseModel):
    """
    A branch-wide closure period.
    Applies to all employees under the agent.
    Used by: POST /api/v1/days-off/
    """
    agent_id: int
    name: Optional[str] = None      # e.g. "Christmas Day", "Renovation closure"
    start_date: date
    end_date: date

    @field_validator("end_date")
    @classmethod
    def end_on_or_after_start(cls, end_date: date, info) -> date:
        start_date = info.data.get("start_date")
        if start_date and end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        return end_date


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas
# ─────────────────────────────────────────────────────────────────────────────

class BusinessHourResponse(BaseModel):
    id: int
    agent_id: int
    day_of_week: int
    open_time: time
    closed_time: time

    model_config = {"from_attributes": True}


class BusinessHoursResponse(BaseModel):
    business_hours: list[BusinessHourResponse]
    total: int


class AgentDayOffResponse(BaseModel):
    id: int
    agent_id: int
    employee_id: Optional[int] = None    # always None for agent-level days off
    name: Optional[str] = None
    start_date: date
    end_date: date

    model_config = {"from_attributes": True}


class AgentDaysOffResponse(BaseModel):
    days_off: list[AgentDayOffResponse]
    total: int


class MessageResponse(BaseModel):
    message: str