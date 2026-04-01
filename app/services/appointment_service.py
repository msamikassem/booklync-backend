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

from datetime import datetime, timedelta
from typing import Optional

import pytz
from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import (
    Appointment,
    AIAgent,
    Customer,
    Employee,
    Service,
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


def _check_no_overlap(
    db: Session,
    employee_id: int,
    start_time: datetime,
    end_time: datetime,
    exclude_appointment_id: Optional[int] = None,
) -> None:
    """
    Check that an employee has no overlapping appointment.
    Raises SlotUnavailableException if there is an overlap.

    Two time ranges overlap if: start1 < end2 AND end1 > start2
    This covers all overlap cases — partial, full containment, exact match.
    """
    query = db.query(Appointment).filter(
        Appointment.employee_id == employee_id,
        Appointment.status.notin_(["cancelled", "no_show"]),
        Appointment.start_time < end_time,
        Appointment.end_time > start_time,
    )

    if exclude_appointment_id:
        query = query.filter(Appointment.id != exclude_appointment_id)

    conflict = query.first()

    if conflict:
        raise SlotUnavailableException(
            detail=(
                f"This employee already has an appointment from "
                f"{conflict.start_time.strftime('%H:%M')} to "
                f"{conflict.end_time.strftime('%H:%M')} UTC"
            )
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
        # UTC times — use for all calculations
        "start_time": appointment.start_time,
        "end_time": appointment.end_time,
        # Local times — use for display
        "start_time_local": start_local,
        "end_time_local": end_local,
        "timezone": timezone_str,
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

    # Step 3 — verify employee
    employee = db.query(Employee).filter(
        Employee.id == data.employee_id,
        Employee.agent_id == data.agent_id,
        Employee.active == True,
    ).first()
    if not employee:
        raise NotFoundException(detail="Employee not found or inactive")

    # Step 4 — verify service
    service = db.query(Service).filter(
        Service.id == data.service_id,
        Service.agent_id == data.agent_id,
        Service.active == True,
    ).first()
    if not service:
        raise NotFoundException(detail="Service not found or inactive")

    # Step 5 — calculate end_time
    end_time = data.start_time + timedelta(minutes=service.duration_minutes)

    # Step 6 — check not in the past
    tz = _get_tz(agent)
    now = datetime.now(tz)
    start_aware = _ensure_tz_aware(data.start_time, tz)

    if start_aware < now:
        raise ConflictException(
            detail="Cannot create an appointment in the past",
            code="PAST_APPOINTMENT"
        )

    # Step 7 — check no overlap
    _check_no_overlap(db, data.employee_id, data.start_time, end_time)

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
        start_time=data.start_time,
        end_time=end_time,
        status=default_status,
        source=data.source,
        note=data.note,
    )

    db.add(appointment)
    db.commit()
    db.refresh(appointment)

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

    # recalculate end_time if start_time changed
    if data.start_time:
        service = db.query(Service).filter(
            Service.id == appointment.service_id
        ).first()
        duration = service.duration_minutes if service else 60
        new_end_time = new_start_time + timedelta(minutes=duration)
    else:
        new_end_time = appointment.end_time

    # check for conflicts if employee or time changed
    if data.employee_id or data.start_time:
        _check_no_overlap(
            db, new_employee_id, new_start_time, new_end_time,
            exclude_appointment_id=appointment_id
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