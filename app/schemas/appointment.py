"""
app/schemas/appointment.py

Pydantic schemas for appointment management.

Time handling:
    start_time / end_time        — UTC, used for all calculations
    start_time_local / end_time_local — agent local time, used for display
    timezone                     — agent IANA timezone string

source field:
    ai           — booked by the AI agent via SMS/call
    dashboard    — manually created from the business dashboard
    booking_page — booked by the customer via the public booking page
    manual       — manually entered offline booking
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum


class AppointmentStatusEnum(str, Enum):
    pending   = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"
    no_show   = "no_show"


class AppointmentSourceEnum(str, Enum):
    booking_page = "booking_page"
    manual       = "manual"
    sms          = "sms"
    phone_call   = "call"
    whats_app    = "whatsapp"


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class AppointmentCreate(BaseModel):
    """
    Data required to create a new appointment.

    start_time must be timezone-aware ISO 8601.
    Example: "2025-07-01T10:00:00+00:00"

    end_time is calculated automatically from the service duration.
    You never send end_time — the system calculates it.
    """
    agent_id: int
    customer_id: int
    employee_id: int
    service_id: int
    start_time: datetime
    source: AppointmentSourceEnum
    note: Optional[str] = None


class AppointmentUpdate(BaseModel):
    """
    Update an existing appointment.
    Only send the fields you want to change.
    Used by: PUT /api/v1/appointments/{id}
    """
    employee_id: Optional[int] = None
    start_time: Optional[datetime] = None
    note: Optional[str] = None


class AppointmentStatusUpdate(BaseModel):
    """
    Update only the status of an appointment.
    Used by: PATCH /api/v1/appointments/{id}/status
    """
    status: AppointmentStatusEnum


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas
# ─────────────────────────────────────────────────────────────────────────────

class AppointmentDetailResponse(BaseModel):
    """
    Full appointment details including related names and local times.

    start_time / end_time           — UTC datetimes, for internal calculations
    start_time_local / end_time_local — same times in the agent's timezone, for display
    timezone                        — agent's IANA timezone string
    """
    id: int
    agent_id: int
    agent_name: str
    customer_id: int
    customer_name: str
    customer_phone: str
    employee_id: int
    employee_name: str
    service_id: Optional[int] = None
    service_name: Optional[str] = None

    # UTC — use for all calculations and comparisons
    start_time: datetime
    end_time: datetime

    # Agent local time — use for displaying to humans
    start_time_local: Optional[datetime] = None
    end_time_local: Optional[datetime] = None
    timezone: Optional[str] = None

    status: str
    source: str
    note: Optional[str] = None
    created_at: datetime


class AppointmentListResponse(BaseModel):
    appointments: list[AppointmentDetailResponse]
    total: int


class MessageResponse(BaseModel):
    message: str