"""
app/services/appointment_service.py

All business logic for appointment management.

Time handling rules:
    - All datetimes stored in UTC in PostgreSQL
    - All overlap checks done in UTC
    - _build_detail() adds local time fields for display
    - AI agent and dashboard always receive both UTC and local time

Creating an appointment runs overlap check first — never double-books.
end_time is always calculated from service duration — never sent by caller.
"""
from app.services.notification_service import send_appointment_notification,send_cancellation,send_confirmation,send_custom,send_no_show_followup
from datetime import datetime, timedelta
from typing import Optional

import pytz
from sqlalchemy.orm import Session, joinedload
from loguru import logger

from app.models.models import (
    Appointment,
    AIAgent,
    Customer,
    Employee,
    EmployeeBranch,
    Service,
    ServiceVariation,
    DayOff,
    BlockedTime,
    BusinessHour,
    EmployeeWorkingHour,
)
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentStatusEnum,
)
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    ConflictException,
    SlotUnavailableException,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """Fetch agent and verify it belongs to the logged in business."""
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")
    return agent


def _get_tz(agent: AIAgent):
    """Get pytz timezone for an agent. Falls back to UTC if invalid."""
    try:
        return pytz.timezone(agent.timezone)
    except Exception:
        return pytz.UTC


def _ensure_tz_aware(dt: datetime, tz) -> datetime:
    """Make a datetime timezone-aware. Assumes UTC if naive."""
    if dt.tzinfo is None:
        return pytz.UTC.localize(dt)
    return dt


def _fmt_local_time(dt_utc: datetime, tz) -> str:
    """Format a UTC datetime as a human-readable local time, e.g. '3:12 PM'."""
    local = _ensure_tz_aware(dt_utc, pytz.UTC).astimezone(tz)
    hour   = local.strftime("%I").lstrip("0") or "12"
    minute = local.strftime("%M")
    ampm   = local.strftime("%p")
    return f"{hour}:{minute} {ampm}"


def _check_no_overlap(
    db: Session,
    employee_id: int,
    start_time: datetime,
    end_time: datetime,
    service: Optional[Service] = None,
    exclude_appointment_id: Optional[int] = None,
    tz=None,
) -> None:
    """
    Check that an employee has no overlapping appointment, accounting for
    preparation buffers on BOTH the new appointment and each existing one.

    The effective blocked window for any appointment is:
        [start - prep_before, end + prep_after]

    A conflict exists when two effective windows overlap:
        new_eff_start < existing_eff_end  AND  new_eff_end > existing_eff_start

    When a conflict is found the exception message distinguishes three cases:
      1. Preceding prep conflict — the existing appointment ends before the new one
         starts, but prep-time buffers cause the windows to touch.  Reports the
         earliest valid start time.
      2. Following prep conflict — the existing appointment starts after the new one
         ends, but prep-time buffers cause the windows to touch.  Reports the latest
         valid start time for this slot.
      3. Direct double-booking — the raw appointment times themselves overlap.
    """
    prep_before_new = timedelta(minutes=(service.preparation_before_minutes or 0) if service else 0)
    prep_after_new  = timedelta(minutes=(service.preparation_after_minutes  or 0) if service else 0)

    # Normalise to UTC-aware so comparisons against DB TIMESTAMPTZ values are safe.
    new_start_utc = _ensure_tz_aware(start_time, pytz.UTC)
    new_end_utc   = _ensure_tz_aware(end_time,   pytz.UTC)

    new_eff_start = new_start_utc - prep_before_new
    new_eff_end   = new_end_utc   + prep_after_new

    # SQL pre-filter: load appointments whose core [start, end] is within 4 hours
    # of our effective window — a generous bound that catches any appointment whose
    # own prep buffers could reach into our slot without over-fetching history.
    _BUFFER = timedelta(hours=4)
    query = (
        db.query(Appointment)
        .options(joinedload(Appointment.service))
        .filter(
            Appointment.employee_id == employee_id,
            Appointment.status.notin_(["cancelled", "no_show"]),
            Appointment.start_time < new_eff_end   + _BUFFER,
            Appointment.end_time   > new_eff_start - _BUFFER,
        )
    )
    if exclude_appointment_id:
        query = query.filter(Appointment.id != exclude_appointment_id)

    for appt in query.all():
        appt_svc = appt.service
        appt_prep_before = timedelta(minutes=(appt_svc.preparation_before_minutes or 0) if appt_svc else 0)
        appt_prep_after  = timedelta(minutes=(appt_svc.preparation_after_minutes  or 0) if appt_svc else 0)

        appt_start_utc = _ensure_tz_aware(appt.start_time, pytz.UTC)
        appt_end_utc   = _ensure_tz_aware(appt.end_time,   pytz.UTC)
        appt_eff_start = appt_start_utc - appt_prep_before
        appt_eff_end   = appt_end_utc   + appt_prep_after

        if not (new_eff_start < appt_eff_end and new_eff_end > appt_eff_start):
            continue

        # ── Conflict detected — classify and build a specific message ──────────

        if appt_end_utc <= new_start_utc:
            # The existing appointment already ENDED before the new one starts, so
            # the conflict is entirely caused by prep-time buffers (not a true overlap).
            min_start_utc = appt_eff_end + prep_before_new

            parts = []
            if appt_prep_after.total_seconds() > 0:
                m = int(appt_prep_after.total_seconds() // 60)
                parts.append(
                    f"the previous appointment requires {m} minute{'s' if m != 1 else ''} "
                    f"of preparation time after it ends"
                )
            if prep_before_new.total_seconds() > 0:
                m = int(prep_before_new.total_seconds() // 60)
                parts.append(
                    f"this service requires {m} minute{'s' if m != 1 else ''} "
                    f"of preparation before the appointment begins"
                )

            detail = (" and ".join(parts)).capitalize() + "."
            if tz:
                detail += f" Earliest available slot: {_fmt_local_time(min_start_utc, tz)}."
            else:
                detail += f" Earliest available start: {min_start_utc.strftime('%H:%M')} UTC."

            raise SlotUnavailableException(detail=detail, code="PREP_TIME_CONFLICT")

        elif appt_start_utc >= new_end_utc:
            # The existing appointment only STARTS after the new one ends, so
            # the conflict is entirely caused by prep-time buffers (not a true overlap).
            duration = new_end_utc - new_start_utc
            latest_start_utc = appt_eff_start - duration - prep_after_new

            parts = []
            if prep_after_new.total_seconds() > 0:
                m = int(prep_after_new.total_seconds() // 60)
                parts.append(
                    f"this service requires {m} minute{'s' if m != 1 else ''} "
                    f"of preparation after the appointment ends"
                )
            if appt_prep_before.total_seconds() > 0:
                m = int(appt_prep_before.total_seconds() // 60)
                parts.append(
                    f"the following appointment requires {m} minute{'s' if m != 1 else ''} "
                    f"of preparation before it begins"
                )

            detail = (" and ".join(parts)).capitalize() + "."
            if tz:
                detail += (
                    f" To allow sufficient preparation time, this service must start"
                    f" no later than {_fmt_local_time(latest_start_utc, tz)}."
                )
            else:
                detail += f" Latest valid start: {latest_start_utc.strftime('%H:%M')} UTC."

            raise SlotUnavailableException(detail=detail, code="PREP_TIME_CONFLICT")

        else:
            # The appointment times themselves overlap — a direct double-booking.
            if tz:
                start_str = _fmt_local_time(appt_start_utc, tz)
                end_str   = _fmt_local_time(appt_end_utc,   tz)
                detail = f"This employee already has an appointment from {start_str} to {end_str}."
            else:
                detail = (
                    f"This employee already has an appointment from "
                    f"{appt_start_utc.strftime('%H:%M')} to "
                    f"{appt_end_utc.strftime('%H:%M')} UTC."
                )
            raise SlotUnavailableException(detail=detail)


def _check_no_blocked_time(
    db: Session,
    employee_id: int,
    start_time: datetime,
    end_time: datetime,
    agent: AIAgent,
) -> None:
    """
    Raise SlotUnavailableException if the appointment overlaps a recurring
    blocked time (break) for this employee.

    Blocked times are stored in the agent's local timezone as day_of_week +
    start_time/end_time, so we localise the appointment date before comparing.
    """
    tz = _get_tz(agent)
    start_local = _ensure_tz_aware(start_time, tz).astimezone(tz)
    appt_date = start_local.date()
    day_of_week = appt_date.weekday()  # Mon=0 .. Sun=6

    blocked_times = db.query(BlockedTime).filter(
        BlockedTime.employee_id == employee_id,
        BlockedTime.agent_id == agent.id,
        BlockedTime.day_of_week == day_of_week,
    ).all()

    for block in blocked_times:
        block_start = tz.localize(datetime.combine(appt_date, block.start_time))
        block_end   = tz.localize(datetime.combine(appt_date, block.end_time))
        start_aware = _ensure_tz_aware(start_time, tz)
        end_aware   = _ensure_tz_aware(end_time, tz)
        if start_aware < block_end and end_aware > block_start:
            raise SlotUnavailableException(
                detail=(
                    f"This time overlaps with a scheduled break "
                    f"({block.start_time.strftime('%H:%M')}–{block.end_time.strftime('%H:%M')})"
                ),
                code="BREAK_CONFLICT",
            )


def _check_business_hours(
    db: Session,
    agent: AIAgent,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """
    Raise SlotUnavailableException if the appointment window falls outside
    the branch's configured business hours for that weekday.

    Working-hours times are stored without timezone in the agent's local tz,
    so we convert the UTC appointment times to local before comparing.
    """
    tz = _get_tz(agent)
    start_local = _ensure_tz_aware(start_time, tz).astimezone(tz)
    appt_date = start_local.date()
    day_of_week = appt_date.weekday()

    business_hour = db.query(BusinessHour).filter(
        BusinessHour.agent_id == agent.id,
        BusinessHour.day_of_week == day_of_week,
    ).first()

    if not business_hour:
        raise SlotUnavailableException(
            detail="Branch is not open on this day of the week",
            code="OUTSIDE_BUSINESS_HOURS",
        )

    branch_open  = tz.localize(datetime.combine(appt_date, business_hour.open_time))
    branch_close = tz.localize(datetime.combine(appt_date, business_hour.closed_time))
    end_local    = _ensure_tz_aware(end_time, tz).astimezone(tz)

    if start_local < branch_open or end_local > branch_close:
        raise SlotUnavailableException(
            detail=(
                f"Appointment falls outside business hours "
                f"({business_hour.open_time.strftime('%H:%M')}–"
                f"{business_hour.closed_time.strftime('%H:%M')})"
            ),
            code="OUTSIDE_BUSINESS_HOURS",
        )


def _check_employee_working_hours(
    db: Session,
    employee_id: int,
    agent: AIAgent,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """
    Raise SlotUnavailableException if the appointment window falls outside
    the employee's working hours for that weekday.
    """
    tz = _get_tz(agent)
    start_local = _ensure_tz_aware(start_time, tz).astimezone(tz)
    appt_date = start_local.date()
    day_of_week = appt_date.weekday()

    working_hour = db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee_id,
        EmployeeWorkingHour.agent_id == agent.id,
        EmployeeWorkingHour.day_of_week == day_of_week,
    ).first()

    if not working_hour:
        raise SlotUnavailableException(
            detail="Employee does not work on this day of the week",
            code="OUTSIDE_WORKING_HOURS",
        )

    emp_start = tz.localize(datetime.combine(appt_date, working_hour.start_time))
    emp_end   = tz.localize(datetime.combine(appt_date, working_hour.end_time))
    end_local = _ensure_tz_aware(end_time, tz).astimezone(tz)

    if start_local < emp_start or end_local > emp_end:
        raise SlotUnavailableException(
            detail=(
                f"Appointment falls outside employee working hours "
                f"({working_hour.start_time.strftime('%H:%M')}–"
                f"{working_hour.end_time.strftime('%H:%M')})"
            ),
            code="OUTSIDE_WORKING_HOURS",
        )


def _build_detail(appointment, agent, customer, employee, service) -> dict:
    """
    Build the full appointment detail dict.

    Includes both UTC times (for calculations) and agent local times
    (for display in dashboard and AI responses to customers).
    """
    start_local = None
    end_local = None
    timezone_str = None

    if agent:
        try:
            tz = _get_tz(agent)
            timezone_str = agent.timezone

            start_utc = _ensure_tz_aware(appointment.start_time, pytz.UTC)
            end_utc = _ensure_tz_aware(appointment.end_time, pytz.UTC)

            start_local = start_utc.astimezone(tz)
            end_local = end_utc.astimezone(tz)

        except Exception as e:
            logger.warning(f"Could not convert appointment times to local: {e}")

    return {
        "id": appointment.id,
        "agent_id": appointment.agent_id,
        "agent_name": agent.agent_name if agent else "Unknown",
        "customer_id": appointment.customer_id,
        "customer_name": customer.name if customer else "Unknown",
        "customer_phone": customer.phone if customer else "",
        "employee_id": appointment.employee_id,
        "employee_name": employee.name if employee else "Unknown",
        "service_id": appointment.service_id,
        "service_name": service.name if service else None,
        "variation_id": appointment.variation_id,
        "variation_name": appointment.variation.name if appointment.variation else None,
        # UTC times — use for all calculations
        "start_time": appointment.start_time,
        "end_time": appointment.end_time,
        # Local times — use for display
        "start_time_local": start_local,
        "end_time_local": end_local,
        "timezone": timezone_str,
        "preparation_before_minutes": service.preparation_before_minutes if service else None,
        "preparation_after_minutes":  service.preparation_after_minutes  if service else None,
        "service_color":              service.color                      if service else None,
        "status": appointment.status,
        "source": appointment.source,
        "note": appointment.note,
        "created_at": appointment.created_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────

def create_appointment(
    db: Session,
    data: AppointmentCreate,
    business_id: int,
) -> dict:
    """
    Create a new appointment.

    Steps:
    1. Verify agent belongs to business
    2. Verify customer belongs to business
    3. Verify employee belongs to agent and is active
    4. Verify service belongs to agent and is active
    5. Calculate end_time from service duration
    6. Check start_time is not in the past
    7. Check no overlapping appointment for this employee
    8. Create the appointment

    Status defaults:
        dashboard / manual  → confirmed
        ai / booking_page   → pending
    """

    # Step 1 — verify agent
    agent = _get_agent_for_business(db, data.agent_id, business_id)

    # Step 2 — verify customer
    customer = db.query(Customer).filter(
        Customer.id == data.customer_id,
        Customer.business_id == business_id,
    ).first()
    if not customer:
        raise NotFoundException(detail="Customer not found")

    # Step 3 — verify employee belongs to this branch and is active + bookable
    # at this branch (per-branch flags live on EmployeeBranch).
    branch = (
        db.query(EmployeeBranch)
        .filter(
            EmployeeBranch.employee_id == data.employee_id,
            EmployeeBranch.agent_id == data.agent_id,
        )
        .first()
    )
    if not branch or not branch.active:
        raise NotFoundException(detail="Employee not found or inactive at this branch")
    if not branch.can_receive_bookings:
        raise ConflictException(
            detail="This employee does not receive bookings at this branch",
            code="EMPLOYEE_NOT_BOOKABLE",
        )
    employee = db.query(Employee).filter(Employee.id == data.employee_id).first()
    if not employee:
        raise NotFoundException(detail="Employee not found")

    # Step 4 — verify service
    service = db.query(Service).filter(
        Service.id == data.service_id,
        Service.agent_id == data.agent_id,
        Service.active == True,
    ).first()
    if not service:
        raise NotFoundException(detail="Service not found or inactive")

    # Step 4b — verify variation (if provided) and resolve duration
    variation = None
    if data.variation_id:
        variation = db.query(ServiceVariation).filter(
            ServiceVariation.id == data.variation_id,
            ServiceVariation.service_id == data.service_id,
            ServiceVariation.active == True,
        ).first()
        if not variation:
            raise NotFoundException(detail="Service variation not found or inactive")
        duration_minutes = variation.duration_minutes
    elif service.duration_minutes:
        duration_minutes = service.duration_minutes
    else:
        raise ConflictException(
            detail="This service requires a variation to be selected",
            code="VARIATION_REQUIRED"
        )

    # Step 5 — calculate end_time
    end_time = data.start_time + timedelta(minutes=duration_minutes)

    # Step 6 — check not in the past
    tz = _get_tz(agent)
    now = datetime.now(tz)
    start_aware = _ensure_tz_aware(data.start_time, tz)

    if start_aware < now:
        raise ConflictException(
            detail="Cannot create an appointment in the past",
            code="PAST_APPOINTMENT"
        )

    # Step 7 — check branch-wide day off
    appt_date = start_aware.date()
    branch_day_off = db.query(DayOff).filter(
        DayOff.agent_id == data.agent_id,
        DayOff.employee_id == None,
        DayOff.start_date <= appt_date,
        DayOff.end_date >= appt_date,
    ).first()
    if branch_day_off:
        raise SlotUnavailableException(
            detail=f"Branch is closed on this date ({branch_day_off.name or 'day off'})",
            code="BRANCH_DAY_OFF"
        )

    # Step 7b — check employee personal day off
    emp_day_off = db.query(DayOff).filter(
        DayOff.agent_id == data.agent_id,
        DayOff.employee_id == data.employee_id,
        DayOff.start_date <= appt_date,
        DayOff.end_date >= appt_date,
    ).first()
    if emp_day_off:
        raise SlotUnavailableException(
            detail=f"Employee has a day off on this date ({emp_day_off.name or 'day off'})",
            code="EMPLOYEE_DAY_OFF"
        )

    # Step 7c — check appointment is within business hours
    _check_business_hours(db, agent, data.start_time, end_time)

    # Step 7d — check appointment is within employee working hours
    _check_employee_working_hours(db, data.employee_id, agent, data.start_time, end_time)

    # Step 7e — check no blocked time (break) conflict
    _check_no_blocked_time(db, data.employee_id, data.start_time, end_time, agent)

    # Step 8 — check no overlap (pass service so prep buffers are respected)
    _check_no_overlap(db, data.employee_id, data.start_time, end_time, service=service, tz=tz)

    # Step 8 — determine default status
    #if data.source.value in ("dashboard", "manual"):
    default_status = AppointmentStatusEnum.confirmed
    #else:
    #    default_status = AppointmentStatusEnum.pending

    # Step 9 — create
    appointment = Appointment(
        agent_id=data.agent_id,
        customer_id=data.customer_id,
        employee_id=data.employee_id,
        service_id=data.service_id,
        variation_id=data.variation_id,
        start_time=data.start_time,
        end_time=end_time,
        status=default_status,
        source=data.source,
        note=data.note,
    )

    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    # send confirmation SMS immediately after booking
    try:
        from app.services.notification_service import send_confirmation
        send_confirmation(db, appointment.id)
    except Exception as e:
        # never let notification failure break the booking
        logger.warning(f"Confirmation SMS failed for appointment {appointment.id}: {e}")

    logger.info(
        f"Appointment created: id={appointment.id} "
        f"customer='{customer.name}' employee='{employee.name}' "
        f"service='{service.name}' at={data.start_time} source={data.source}"
    )

    return _build_detail(appointment, agent, customer, employee, service)


# ─────────────────────────────────────────────────────────────────────────────
# GET LIST
# ─────────────────────────────────────────────────────────────────────────────

def get_appointments(
    db: Session,
    agent_id: int,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    employee_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
) -> dict:
    """
    Get appointments for an agent with optional filters.
    Results ordered by start_time ascending.
    """

    agent = _get_agent_for_business(db, agent_id, business_id)

    query = db.query(Appointment).filter(
        Appointment.agent_id == agent_id
    )

    if date_from:
        query = query.filter(Appointment.start_time >= date_from)
    if date_to:
        query = query.filter(Appointment.start_time <= date_to)
    if employee_id:
        query = query.filter(Appointment.employee_id == employee_id)
    if customer_id:
        query = query.filter(Appointment.customer_id == customer_id)
    if status:
        query = query.filter(Appointment.status == status)

    appointments = query.order_by(Appointment.start_time).all()

    details = []
    for appt in appointments:
        customer = db.query(Customer).filter(Customer.id == appt.customer_id).first()
        employee = db.query(Employee).filter(Employee.id == appt.employee_id).first()
        service = db.query(Service).filter(Service.id == appt.service_id).first() if appt.service_id else None
        details.append(_build_detail(appt, agent, customer, employee, service))

    return {
        "appointments": details,
        "total": len(details),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET ONE
# ─────────────────────────────────────────────────────────────────────────────

def get_appointment(
    db: Session,
    appointment_id: int,
    business_id: int,
) -> dict:
    """Get one specific appointment by id."""

    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id
    ).first()

    if not appointment:
        raise NotFoundException(detail="Appointment not found")

    agent = _get_agent_for_business(db, appointment.agent_id, business_id)
    customer = db.query(Customer).filter(Customer.id == appointment.customer_id).first()
    employee = db.query(Employee).filter(Employee.id == appointment.employee_id).first()
    service = db.query(Service).filter(Service.id == appointment.service_id).first() if appointment.service_id else None

    return _build_detail(appointment, agent, customer, employee, service)


def resolve_appointment_agent_id(db: Session, appointment_id: int, business_id: int) -> int:
    """
    Return the branch (agent) id of an appointment after verifying it belongs to
    the tenant. Lets the router enforce branch-level RBAC before mutating.
    """
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise NotFoundException(detail="Appointment not found")
    _get_agent_for_business(db, appointment.agent_id, business_id)
    return appointment.agent_id


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update_appointment(
    db: Session,
    appointment_id: int,
    data: AppointmentUpdate,
    business_id: int,
) -> dict:
    """
    Update an appointment — reschedule or change employee.

    If start_time changes, recalculates end_time automatically.
    Runs overlap check with the exclude flag so the appointment
    doesn't conflict with itself.
    Cannot update cancelled, completed, or no_show appointments.
    """

    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id
    ).first()

    if not appointment:
        raise NotFoundException(detail="Appointment not found")

    agent = _get_agent_for_business(db, appointment.agent_id, business_id)

    # cannot update closed appointments
    if appointment.status in ("cancelled", "completed", "no_show"):
        raise ConflictException(
            detail=f"Cannot update a {appointment.status} appointment",
            code="APPOINTMENT_CLOSED"
        )

    new_employee_id = data.employee_id or appointment.employee_id
    new_start_time = data.start_time or appointment.start_time

    # when reassigning, verify the new employee belongs to this branch and
    # is active + bookable there.
    if data.employee_id:
        new_branch = (
            db.query(EmployeeBranch)
            .filter(
                EmployeeBranch.employee_id == data.employee_id,
                EmployeeBranch.agent_id == appointment.agent_id,
            )
            .first()
        )
        if not new_branch or not new_branch.active:
            raise NotFoundException(detail="Employee not found or inactive at this branch")
        if not new_branch.can_receive_bookings:
            raise ConflictException(
                detail="This employee does not receive bookings at this branch",
                code="EMPLOYEE_NOT_BOOKABLE",
            )

    # always load service — needed for prep-time checks and duration recalculation
    service = db.query(Service).filter(Service.id == appointment.service_id).first()

    # recalculate end_time if start_time changed
    if data.start_time:
        duration = service.duration_minutes if service else 60
        new_end_time = new_start_time + timedelta(minutes=duration)
    else:
        new_end_time = appointment.end_time

    # check for conflicts if employee or time changed
    if data.employee_id or data.start_time:
        tz = _get_tz(agent)
        _check_business_hours(db, agent, new_start_time, new_end_time)
        _check_employee_working_hours(db, new_employee_id, agent, new_start_time, new_end_time)
        _check_no_blocked_time(db, new_employee_id, new_start_time, new_end_time, agent)
        _check_no_overlap(
            db, new_employee_id, new_start_time, new_end_time,
            service=service,
            exclude_appointment_id=appointment_id,
            tz=tz,
        )

    # apply updates
    if data.employee_id:
        appointment.employee_id = data.employee_id
    if data.start_time:
        appointment.start_time = new_start_time
        appointment.end_time = new_end_time
    if data.note is not None:
        appointment.note = data.note

    db.commit()
    db.refresh(appointment)
    # send reschedule SMS if time or employee changed
    if data.start_time or data.employee_id:
        try:
            from app.services.notification_service import send_reschedule
            send_reschedule(db, appointment.id)
        except Exception as e:
            logger.warning(f"Reschedule SMS failed for appointment {appointment.id}: {e}")

    customer = db.query(Customer).filter(Customer.id == appointment.customer_id).first()
    employee = db.query(Employee).filter(Employee.id == appointment.employee_id).first()
    service = db.query(Service).filter(Service.id == appointment.service_id).first() if appointment.service_id else None

    logger.info(f"Appointment updated: id={appointment_id}")

    return _build_detail(appointment, agent, customer, employee, service)


# ─────────────────────────────────────────────────────────────────────────────
# STATUS UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update_appointment_status(
    db: Session,
    appointment_id: int,
    status: AppointmentStatusEnum,
    business_id: int,
) -> dict:
    """Update the status of an appointment."""

    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id
    ).first()

    if not appointment:
        raise NotFoundException(detail="Appointment not found")

    agent = _get_agent_for_business(db, appointment.agent_id, business_id)

    appointment.status = status
    db.commit()
    db.refresh(appointment)

    # send no-show follow-up if status was just set to no_show
    if status == AppointmentStatusEnum.no_show:
        try:
            from app.services.notification_service import send_no_show_followup
            send_no_show_followup(db, appointment.id)
        except Exception as e:
            logger.warning(f"No-show SMS failed for appointment {appointment.id}: {e}")

    customer = db.query(Customer).filter(Customer.id == appointment.customer_id).first()
    employee = db.query(Employee).filter(Employee.id == appointment.employee_id).first()
    service = db.query(Service).filter(Service.id == appointment.service_id).first() if appointment.service_id else None

    logger.info(f"Appointment status: id={appointment_id} → {status}")

    return _build_detail(appointment, agent, customer, employee, service)


def cancel_appointment(
    db: Session,
    appointment_id: int,
    business_id: int,
) -> dict:
    """Cancel an appointment — shortcut for status update."""
    # send cancellation SMS
    try:
        from app.services.notification_service import send_cancellation
        send_cancellation(db, appointment_id)
    except Exception as e:
        logger.warning(f"Cancellation SMS failed for appointment {appointment_id}: {e}")

    return update_appointment_status(
        db, appointment_id, AppointmentStatusEnum.cancelled, business_id
    )
    

# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER HISTORY
# ─────────────────────────────────────────────────────────────────────────────

def get_customer_appointments(
    db: Session,
    customer_id: int,
    business_id: int,
) -> dict:
    """
    Get all appointments for a specific customer.
    Used by the AI agent to get customer booking history.
    Sorted newest first.
    """

    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.business_id == business_id,
    ).first()

    if not customer:
        raise NotFoundException(detail="Customer not found")

    appointments = db.query(Appointment).filter(
        Appointment.customer_id == customer_id
    ).order_by(Appointment.start_time.desc()).all()

    details = []
    for appt in appointments:
        agent = db.query(AIAgent).filter(AIAgent.id == appt.agent_id).first()
        employee = db.query(Employee).filter(Employee.id == appt.employee_id).first()
        service = db.query(Service).filter(Service.id == appt.service_id).first() if appt.service_id else None
        details.append(_build_detail(appt, agent, customer, employee, service))

    return {
        "appointments": details,
        "total": len(details),
    }