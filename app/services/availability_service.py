"""
app/services/availability_service.py

The availability engine.

Handles four operations:
    1. get_availability()        — all slots on a date (authenticated)
    2. check_slot()              — check specific time, return closest if unavailable
    3. get_public_availability() — all slots on a date (public)
    4. get_agent_time()          — current local time for an agent

PAST SLOT FILTERING:
    Any slot starting before the current time is excluded.
    Uses the agent's timezone to determine "now" correctly.

MINIMUM ADVANCE TIME:
    MIN_ADVANCE_MINUTES — buffer before current time.
    Default 60 minutes — no same-hour bookings.

MAXIMUM ADVANCE DAYS:
    MAX_ADVANCE_DAYS — how far ahead bookings are allowed.
    Default 60 days.

CLOSEST SLOTS COUNT:
    CLOSEST_SLOTS_COUNT — how many closest slots to return
    when the requested time is unavailable.
    Default 3 on each side (before and after).
"""

from datetime import datetime, date, time, timedelta
from typing import Optional
import pytz

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import (
    AIAgent,
    Service,
    Employee,
    BusinessHour,
    EmployeeWorkingHour,
    BlockedTime,
    DayOff,
    Appointment,
    EmployeeService,
)
from app.schemas.availability import (
    AvailabilityResponse,
    SlotCheckResponse,
    AgentTimeResponse,
    EmployeeSlot,
)
from app.core.exceptions import NotFoundException, ForbiddenException


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# Change these values to adjust booking behaviour globally.
# Later these can be moved to per-agent settings in the database.
# ─────────────────────────────────────────────────────────────────────────────
SLOT_INTERVAL_MINUTES = 15      # how often slots are generated
MIN_ADVANCE_MINUTES = 60        # minimum time before a slot can be booked
MAX_ADVANCE_DAYS = 60           # maximum days ahead a booking can be made
CLOSEST_SLOTS_COUNT = 3         # how many closest slots to return on each side


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 — all slots on a date (authenticated)
# ─────────────────────────────────────────────────────────────────────────────

def get_availability(
    db: Session,
    agent_id: int,
    service_id: int,
    requested_date: date,
    employee_id: Optional[int],
    business_id: int,
) -> AvailabilityResponse:
    """
    Returns all available booking slots for a given date and service.
    Requires business ownership of the agent.

    Args:
        db:             database session
        agent_id:       which branch to check
        service_id:     which service (determines duration)
        requested_date: which calendar date to check
        employee_id:    optional — filter to one specific employee
        business_id:    id of the logged in business

    Returns:
        AvailabilityResponse with list of available slots
    """

    agent, service, tz = _verify_and_load(
        db, agent_id, service_id, business_id=business_id
    )

    return _build_availability_response(
        db, agent, service, requested_date, employee_id, tz
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — check specific time
# ─────────────────────────────────────────────────────────────────────────────

def check_slot(
    db: Session,
    agent_id: int,
    service_id: int,
    requested_start: datetime,
    employee_id: Optional[int],
    business_id: int,
) -> SlotCheckResponse:
    """
    Check if a specific date and time is available.

    If available — returns the slot.
    If not available — returns the N closest slots on each side.

    Args:
        db:              database session
        agent_id:        which branch to check
        service_id:      which service
        requested_start: the exact datetime the customer wants
                         must be timezone-aware
        employee_id:     optional — filter to one specific employee
        business_id:     id of the logged in business

    Returns:
        SlotCheckResponse
    """

    agent, service, tz = _verify_and_load(
        db, agent_id, service_id, business_id=business_id
    )

    requested_date = requested_start.astimezone(tz).date()
    requested_end = requested_start + timedelta(minutes=service.duration_minutes)

    # get all available slots for that date
    all_slots = _get_all_slots(
        db, agent, service, requested_date, employee_id, tz
    )

    # find ALL employees who have this exact slot available
    # not just the first one
    matched_slots = []
    for slot in all_slots:
        slot_start_normalized = slot.start_time.astimezone(tz)
        requested_normalized = requested_start.astimezone(tz)

        if slot_start_normalized == requested_normalized:
            matched_slots.append(slot)

    is_available = len(matched_slots) > 0

    base = SlotCheckResponse(
        agent_id=agent_id,
        service_id=service_id,
        service_name=service.name,
        duration_minutes=service.duration_minutes,
        requested_start=requested_start,
        requested_end=requested_end,
        available=is_available,
        # slot is now a list of all available employees for that time
        slots=matched_slots if is_available else [],
    )

    if is_available:
        return base

    # slot not available — find closest on each side
    slots_before = [s for s in all_slots if s.start_time < requested_start]
    slots_after  = [s for s in all_slots if s.start_time > requested_start]

    base.closest_before = slots_before[-CLOSEST_SLOTS_COUNT:] if slots_before else []
    base.closest_after  = slots_after[:CLOSEST_SLOTS_COUNT]   if slots_after  else []

    return base


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — all slots on a date (public)
# ─────────────────────────────────────────────────────────────────────────────

def get_public_availability(
    db: Session,
    agent_id: int,
    service_id: int,
    requested_date: date,
    employee_id: Optional[int],
) -> AvailabilityResponse:
    """
    Public availability check — no business ownership required.

    Used by the customer-facing booking page.
    Only works for active agents and active services.

    Args:
        db:             database session
        agent_id:       which branch to check
        service_id:     which service
        requested_date: which date to check
        employee_id:    optional employee filter

    Returns:
        AvailabilityResponse with list of available slots
    """

    agent, service, tz = _verify_and_load(
        db, agent_id, service_id, public=True
    )

    return _build_availability_response(
        db, agent, service, requested_date, employee_id, tz
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 4 — current agent local time
# ─────────────────────────────────────────────────────────────────────────────

def get_agent_time(
    db: Session,
    agent_id: int,
    business_id: int,
) -> AgentTimeResponse:
    """
    Returns the current local time for an agent based on their timezone.

    Also returns whether the branch is currently open and
    today's opening and closing times.

    Args:
        db:          database session
        agent_id:    which agent to check
        business_id: id of the logged in business

    Returns:
        AgentTimeResponse
    """

    # verify agent
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    # get timezone
    try:
        tz = pytz.timezone(agent.timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC

    # get current time in UTC and in agent timezone
    utc_now = datetime.now(pytz.UTC)
    local_now = utc_now.astimezone(tz)
    local_date = local_now.date()
    day_of_week = local_date.weekday()

    # check if branch is currently open
    business_hour = db.query(BusinessHour).filter(
        BusinessHour.agent_id == agent_id,
        BusinessHour.day_of_week == day_of_week,
    ).first()

    is_open = False
    open_time_str = None
    close_time_str = None

    if business_hour:
        open_dt = tz.localize(datetime.combine(local_date, business_hour.open_time))
        close_dt = tz.localize(datetime.combine(local_date, business_hour.closed_time))

        # check branch is not on a day off
        agent_day_off = db.query(DayOff).filter(
            DayOff.agent_id == agent_id,
            DayOff.employee_id == None,
            DayOff.start_date <= local_date,
            DayOff.end_date >= local_date,
        ).first()

        if not agent_day_off:
            is_open = open_dt <= local_now < close_dt
            open_time_str = business_hour.open_time.strftime("%H:%M")
            close_time_str = business_hour.closed_time.strftime("%H:%M")

    logger.info(
        f"Agent time check: agent={agent_id} timezone={agent.timezone} "
        f"local={local_now.strftime('%Y-%m-%d %H:%M:%S')} is_open={is_open}"
    )

    return AgentTimeResponse(
        agent_id=agent_id,
        timezone=agent.timezone,
        current_time=local_now,
        current_date=local_date,
        utc_time=utc_now,
        is_open=is_open,
        open_time=open_time_str,
        close_time=close_time_str,
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL — shared logic
# ─────────────────────────────────────────────────────────────────────────────

def _verify_and_load(
    db: Session,
    agent_id: int,
    service_id: int,
    business_id: Optional[int] = None,
    public: bool = False,
):
    """
    Verify agent and service exist and are accessible.
    Returns (agent, service, tz).

    For authenticated requests: checks business ownership.
    For public requests: only checks agent is active.
    """

    # load agent
    agent_query = db.query(AIAgent).filter(AIAgent.id == agent_id)
    if public:
        agent_query = agent_query.filter(AIAgent.active == True)
    agent = agent_query.first()

    if not agent:
        raise NotFoundException(
            detail="Agent not found" if not public else "Agent not found or inactive"
        )

    if not public and business_id is not None:
        if agent.business_id != business_id:
            raise ForbiddenException(detail="You do not have access to this agent")

    # load service
    service = db.query(Service).filter(
        Service.id == service_id,
        Service.agent_id == agent_id,
        Service.active == True,
    ).first()

    if not service:
        raise NotFoundException(detail="Service not found or inactive")

    # get timezone
    try:
        tz = pytz.timezone(agent.timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{agent.timezone}', using UTC")
        tz = pytz.UTC

    return agent, service, tz


def _build_availability_response(
    db: Session,
    agent,
    service,
    requested_date: date,
    employee_id: Optional[int],
    tz,
) -> AvailabilityResponse:
    """
    Build the full availability response for a date.
    Shared by both authenticated and public endpoints.
    """

    # validate date range
    local_now = datetime.now(tz)
    local_today = local_now.date()

    if requested_date < local_today:
        # past date — return empty
        return _empty_response(agent.id, service.id, requested_date, service)

    if requested_date > local_today + timedelta(days=MAX_ADVANCE_DAYS):
        # too far in the future — return empty
        return _empty_response(agent.id, service.id, requested_date, service)

    all_slots = _get_all_slots(db, agent, service, requested_date, employee_id, tz)

    return AvailabilityResponse(
        agent_id=agent.id,
        service_id=service.id,
        date=requested_date,
        service_name=service.name,
        duration_minutes=service.duration_minutes,
        available_slots=all_slots,
        total_slots=len(all_slots),
    )


def _get_all_slots(
    db: Session,
    agent,
    service,
    requested_date: date,
    employee_id: Optional[int],
    tz,
) -> list[EmployeeSlot]:
    """
    Core slot generation. Runs all eight checks and returns valid slots.
    """

    day_of_week = requested_date.weekday()

    # check branch is open this day
    business_hour = db.query(BusinessHour).filter(
        BusinessHour.agent_id == agent.id,
        BusinessHour.day_of_week == day_of_week,
    ).first()

    if not business_hour:
        return []

    # check branch is not on a day off
    agent_day_off = db.query(DayOff).filter(
        DayOff.agent_id == agent.id,
        DayOff.employee_id == None,
        DayOff.start_date <= requested_date,
        DayOff.end_date >= requested_date,
    ).first()

    if agent_day_off:
        return []

    # build branch open/close datetimes
    branch_open = tz.localize(datetime.combine(requested_date, business_hour.open_time))
    branch_close = tz.localize(datetime.combine(requested_date, business_hour.closed_time))

    # get eligible employees
    employees_query = db.query(Employee).join(
        EmployeeService,
        Employee.id == EmployeeService.employee_id,
    ).filter(
        Employee.agent_id == agent.id,
        Employee.active == True,
        EmployeeService.service_id == service.id,
    )

    if employee_id is not None:
        employees_query = employees_query.filter(Employee.id == employee_id)

    employees = employees_query.all()

    if not employees:
        return []

    # generate slots for each employee
    all_slots: list[EmployeeSlot] = []
    for employee in employees:
        employee_slots = _get_employee_slots(
            db=db,
            employee=employee,
            service=service,
            requested_date=requested_date,
            day_of_week=day_of_week,
            branch_open=branch_open,
            branch_close=branch_close,
            tz=tz,
        )
        all_slots.extend(employee_slots)

    # sort chronologically
    all_slots.sort(key=lambda s: s.start_time)

    return all_slots


def _get_employee_slots(
    db: Session,
    employee,
    service,
    requested_date: date,
    day_of_week: int,
    branch_open: datetime,
    branch_close: datetime,
    tz,
) -> list[EmployeeSlot]:
    """
    Generate all valid slots for one employee on one date.
    Excludes slots in the past or within MIN_ADVANCE_MINUTES.
    """

    # check employee works this day
    working_hour = db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee.id,
        EmployeeWorkingHour.day_of_week == day_of_week,
    ).first()

    if not working_hour:
        return []

    # check employee is not on personal leave
    personal_day_off = db.query(DayOff).filter(
        DayOff.employee_id == employee.id,
        DayOff.start_date <= requested_date,
        DayOff.end_date >= requested_date,
    ).first()

    if personal_day_off:
        return []

    # build effective working window
    employee_start = tz.localize(datetime.combine(requested_date, working_hour.start_time))
    employee_end = tz.localize(datetime.combine(requested_date, working_hour.end_time))

    effective_start = max(employee_start, branch_open)
    effective_end = min(employee_end, branch_close)

    if effective_start >= effective_end:
        return []

    # get existing appointments for this employee today
    day_start = tz.localize(datetime.combine(requested_date, time(0, 0, 0)))
    day_end = tz.localize(datetime.combine(requested_date, time(23, 59, 59)))

    existing_appointments = db.query(Appointment).filter(
        Appointment.employee_id == employee.id,
        Appointment.start_time >= day_start,
        Appointment.start_time <= day_end,
        Appointment.status.notin_(["cancelled", "no_show"]),
    ).all()

    # get blocked times for this day of week
    blocked_times = db.query(BlockedTime).filter(
        BlockedTime.employee_id == employee.id,
        BlockedTime.day_of_week == day_of_week,
    ).all()

    # earliest bookable time — current time + MIN_ADVANCE_MINUTES
    earliest_bookable = datetime.now(tz) + timedelta(minutes=MIN_ADVANCE_MINUTES)

    # generate candidate slots
    service_duration = timedelta(minutes=service.duration_minutes)
    slot_interval = timedelta(minutes=SLOT_INTERVAL_MINUTES)

    valid_slots = []
    current = effective_start

    while current + service_duration <= effective_end:
        slot_end = current + service_duration

        # skip slots in the past or within minimum advance time
        if current < earliest_bookable:
            current += slot_interval
            continue

        # check against existing appointments
        if _overlaps_appointments(current, slot_end, existing_appointments, tz):
            current += slot_interval
            continue

        # check against blocked times
        if _overlaps_blocked_times(current, slot_end, blocked_times, requested_date, tz):
            current += slot_interval
            continue

        valid_slots.append(EmployeeSlot(
            employee_id=employee.id,
            employee_name=employee.name,
            start_time=current,
            end_time=slot_end,
        ))

        current += slot_interval

    return valid_slots


def _overlaps_appointments(
    slot_start: datetime,
    slot_end: datetime,
    appointments: list,
    tz,
) -> bool:
    """
    Returns True if the slot overlaps any existing appointment.
    Uses the standard overlap formula: start1 < end2 AND end1 > start2
    """
    for appt in appointments:
        appt_start = _ensure_tz_aware(appt.start_time, tz)
        appt_end = _ensure_tz_aware(appt.end_time, tz)
        if slot_start < appt_end and slot_end > appt_start:
            return True
    return False


def _overlaps_blocked_times(
    slot_start: datetime,
    slot_end: datetime,
    blocked_times: list,
    requested_date: date,
    tz,
) -> bool:
    """
    Returns True if the slot overlaps any blocked time.
    Blocked times are recurring weekly — combined with the requested date.
    """
    for block in blocked_times:
        block_start = tz.localize(datetime.combine(requested_date, block.start_time))
        block_end = tz.localize(datetime.combine(requested_date, block.end_time))
        if slot_start < block_end and slot_end > block_start:
            return True
    return False


def _ensure_tz_aware(dt: datetime, tz) -> datetime:
    """Ensure a datetime is timezone-aware."""
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt


def _empty_response(
    agent_id: int,
    service_id: int,
    requested_date: date,
    service,
) -> AvailabilityResponse:
    """Return an empty availability response."""
    return AvailabilityResponse(
        agent_id=agent_id,
        service_id=service_id,
        date=requested_date,
        service_name=service.name,
        duration_minutes=service.duration_minutes,
        available_slots=[],
        total_slots=0,
    )