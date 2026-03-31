"""
app/schemas/availability.py

Pydantic schemas for the availability engine.

Four endpoints:
    1. All available slots on a date (authenticated)
    2. Check a specific time — returns closest slots if unavailable
    3. All available slots on a date (public, no auth)
    4. Current local time for an agent
"""

from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


# ─────────────────────────────────────────────────────────────────────────────
# SHARED — used across multiple responses
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeSlot(BaseModel):
    """A single available time slot for one employee."""
    employee_id: int
    employee_name: str
    start_time: datetime
    end_time: datetime


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 & 3 — all slots on a date
# ─────────────────────────────────────────────────────────────────────────────

class AvailabilityResponse(BaseModel):
    """
    All available slots for a given date and service.
    Used by both the authenticated and public date endpoints.
    """
    agent_id: int
    service_id: int
    date: date
    service_name: str
    duration_minutes: int
    available_slots: list[EmployeeSlot]
    total_slots: int


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — check specific time
# ─────────────────────────────────────────────────────────────────────────────

class SlotCheckResponse(BaseModel):
    """
    Response for checking a specific date and time.

    If the requested time is available:
        available = True
        slot = the requested slot
        closest_before = empty
        closest_after = empty

    If the requested time is NOT available:
        available = False
        slot = None
        closest_before = N closest slots before the requested time
        closest_after  = N closest slots after the requested time
    """
    agent_id: int
    service_id: int
    service_name: str
    duration_minutes: int
    requested_start: datetime
    requested_end: datetime
    available: bool

    slots: list[EmployeeSlot] = []
    closest_before: list[EmployeeSlot] = []
    closest_after: list[EmployeeSlot] = []


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 4 — current agent local time
# ─────────────────────────────────────────────────────────────────────────────

class AgentTimeResponse(BaseModel):
    """
    Current local time for an agent based on their configured timezone.
    """
    agent_id: int
    timezone: str
    current_time: datetime
    current_date: date
    utc_time: datetime
    is_open: bool
    open_time: Optional[str] = None
    close_time: Optional[str] = None