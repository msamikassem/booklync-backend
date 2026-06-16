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

from pydantic import BaseModel, Field
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
    variation_id: Optional[int] = None
    start_time: datetime
    source: AppointmentSourceEnum
    note: Optional[str] = None


class AppointmentItem(BaseModel):
    """
    One service line in a multi-service ("book several services in one visit")
    request. Each line carries its own employee and optional variation; the
    backend schedules the lines back-to-back in the order given.
    """
    service_id: int
    variation_id: Optional[int] = None
    employee_id: int


class MultiAppointmentCreate(BaseModel):
    """
    Book several services for one customer in a single visit.

    The services in `items` are scheduled back-to-back starting at `start_time`
    (in the order given): the first runs at `start_time`, each subsequent service
    begins when the previous one ends. Every line becomes its own independent
    Appointment (own status / sale on completion); they are validated as a set
    and committed atomically — if any line is unbookable, none are created.

    end_time of each line is derived from its service/variation duration.
    """
    agent_id: int
    customer_id: int
    start_time: datetime
    source: AppointmentSourceEnum
    note: Optional[str] = None
    items: list[AppointmentItem] = Field(..., min_length=1)


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
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    employee_id: Optional[int] = None
    employee_name: str
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    variation_id: Optional[int] = None
    variation_name: Optional[str] = None

    # UTC — use for all calculations and comparisons
    start_time: datetime
    end_time: datetime

    # Agent local time — use for displaying to humans
    start_time_local: Optional[datetime] = None
    end_time_local: Optional[datetime] = None
    timezone: Optional[str] = None

    preparation_before_minutes: Optional[int] = None
    preparation_after_minutes: Optional[int] = None
    service_color: Optional[str] = None

    status: str
    source: str
    note: Optional[str] = None
    # Set when this appointment was booked as part of a multi-service visit;
    # all lines of one visit share the same id. NULL for single bookings.
    booking_group_id: Optional[str] = None
    created_at: datetime


class AppointmentListResponse(BaseModel):
    appointments: list[AppointmentDetailResponse]
    total: int


class MessageResponse(BaseModel):
    message: str