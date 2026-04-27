"""
app/services/notification_service.py

Database-driven notification system.

NO LLM dependency — everything is template-based and database-driven.
The scheduler queries the database directly and sends SMS via Twilio.

Notification types:
    reminder        — sent X minutes before an appointment
    confirmation    — sent immediately after booking
    cancellation    — sent when appointment is cancelled
    reschedule      — sent when appointment is rescheduled
    no_show_followup— sent after a missed appointment
    custom          — any custom message triggered manually

Architecture:
    DEFAULT_TEMPLATES    — reusable message templates with {placeholders}
    DEFAULT_NOTIFICATION_RULES — configurable reminder windows per agent
    _notification_log    — in-memory set preventing duplicate sends
    send_appointment_notification() — core send function used by everything
    run_reminder_check() — scheduler job that runs every minute

All times are handled in the agent's local timezone.
No LLM involvement anywhere in this file.
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
from app.utils.twilio_utils import send_sms


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE TEMPLATES
# {placeholders} are filled from appointment data at send time.
# Add new templates here — no code changes needed elsewhere.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_TEMPLATES = {
    "reminder_24h": (
        "Hi {customer_name}, this is a reminder that you have a {service_name} "
        "appointment with {employee_name} tomorrow at {appointment_time} at {location}. "
        "Reply CANCEL to cancel or call us to reschedule."
    ),
    "reminder_2h": (
        "Hi {customer_name}, your {service_name} appointment with {employee_name} "
        "is in 2 hours at {appointment_time}. See you soon at {location}!"
    ),
    "reminder_1h": (
        "Hi {customer_name}, your {service_name} appointment with {employee_name} "
        "is in 1 hour at {appointment_time} at {location}. See you soon!"
    ),
    "reminder_30m": (
        "Hi {customer_name}, just a heads up — your {service_name} with "
        "{employee_name} starts in 30 minutes at {appointment_time}. See you shortly!"
    ),
    "reminder_custom": (
        "Hi {customer_name}, reminder: {service_name} with {employee_name} "
        "at {appointment_time} at {location}."
    ),
    "confirmation": (
        "Hi {customer_name}, your {service_name} appointment is confirmed! "
        "{employee_name} will see you on {appointment_date} at {appointment_time} "
        "at {location}. Reply CANCEL to cancel."
    ),
    "cancellation": (
        "Hi {customer_name}, your {service_name} appointment on {appointment_date} "
        "at {appointment_time} has been cancelled. "
        "Contact us to rebook — we hope to see you soon!"
    ),
    "reschedule": (
        "Hi {customer_name}, your appointment has been rescheduled. "
        "Your new {service_name} appointment with {employee_name} is on "
        "{appointment_date} at {appointment_time} at {location}."
    ),
    "no_show_followup": (
        "Hi {customer_name}, we missed you today for your {service_name} appointment. "
        "We'd love to see you again — contact us to rebook!"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION RULES
# Defines when reminders are sent for every agent.
# minutes_before controls the reminder window.
# Add new rules here to add new reminder windows globally.
# In Phase 2 these move to a database table for per-agent configuration.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_NOTIFICATION_RULES = [
    {
        "type": "reminder",
        "name": "24 hour reminder",
        "minutes_before": 1440,
        "template_key": "reminder_24h",
        "enabled": True,
    },
    {
        "type": "reminder",
        "name": "2 hour reminder",
        "minutes_before": 120,
        "template_key": "reminder_2h",
        "enabled": True,
    },
    {
        "type": "reminder",
        "name": "1 hour reminder",
        "minutes_before": 60,
        "template_key": "reminder_1h",
        "enabled": True,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY NOTIFICATION LOG
# Prevents sending the same notification twice.
# Key format: "{appointment_id}:{notification_type}:{minutes_before}"
# Phase 2: move to a database table so it survives server restarts.
# ─────────────────────────────────────────────────────────────────────────────

_notification_log: set = set()


def _notification_key(
    appointment_id: int,
    notification_type: str,
    minutes_before: int = 0,
) -> str:
    """Generate a unique deduplication key for a notification."""
    return f"{appointment_id}:{notification_type}:{minutes_before}"


def _already_sent(
    appointment_id: int,
    notification_type: str,
    minutes_before: int = 0,
) -> bool:
    """Return True if this exact notification was already sent this session."""
    return _notification_key(appointment_id, notification_type, minutes_before) in _notification_log


def _mark_sent(
    appointment_id: int,
    notification_type: str,
    minutes_before: int = 0,
) -> None:
    """Record that this notification was sent."""
    _notification_log.add(_notification_key(appointment_id, notification_type, minutes_before))


# ─────────────────────────────────────────────────────────────────────────────
# TEMPLATE RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_template(
    template_key: str,
    appointment: Appointment,
    agent: AIAgent,
    customer: Customer,
    employee: Optional[Employee],
    service: Optional[Service],
    custom_template: Optional[str] = None,
) -> str:
    """
    Render a notification template with real appointment data.

    Fills all {placeholder} values. Unknown placeholders are left as-is
    and a warning is logged rather than crashing.

    Available placeholders:
        {customer_name}         first name only
        {customer_full_name}    full name
        {service_name}          name of the service
        {employee_name}         first name of the employee
        {employee_full_name}    full name of the employee
        {appointment_time}      e.g. "10:30 AM"
        {appointment_date}      e.g. "Tuesday, June 15"
        {appointment_datetime}  e.g. "Tuesday, June 15 at 10:30 AM"
        {location}              agent location name
        {agent_name}            agent name
        {appointment_id}        appointment ID number

    Args:
        template_key:    key into DEFAULT_TEMPLATES
        appointment:     Appointment object
        agent:           AIAgent object
        customer:        Customer object
        employee:        Employee object (may be None)
        service:         Service object (may be None)
        custom_template: if provided, use this instead of DEFAULT_TEMPLATES

    Returns:
        str — rendered message ready to send, or empty string on failure
    """

    template = custom_template or DEFAULT_TEMPLATES.get(template_key, "")

    if not template:
        logger.warning(f"No template found for key '{template_key}'")
        return ""

    # convert appointment times to agent local timezone
    try:
        tz = pytz.timezone(agent.timezone)
    except Exception:
        tz = pytz.UTC

    start_utc = appointment.start_time
    if start_utc.tzinfo is None:
        start_utc = pytz.UTC.localize(start_utc)
    start_local = start_utc.astimezone(tz)

    # human readable formats
    appointment_time = start_local.strftime("%-I:%M %p")
    appointment_date = start_local.strftime("%A, %B %-d")
    appointment_datetime = start_local.strftime("%A, %B %-d at %-I:%M %p")

    placeholders = {
        "customer_name": customer.name.split()[0] if customer.name else "there",
        "customer_full_name": customer.name or "Customer",
        "service_name": service.name if service else "appointment",
        "employee_name": employee.name.split()[0] if employee and employee.name else "your stylist",
        "employee_full_name": employee.name if employee else "your stylist",
        "appointment_time": appointment_time,
        "appointment_date": appointment_date,
        "appointment_datetime": appointment_datetime,
        "location": agent.location_name,
        "agent_name": agent.agent_name,
        "appointment_id": str(appointment.id),
    }

    try:
        rendered = template.format(**placeholders)
    except KeyError as e:
        logger.warning(f"Template '{template_key}' has unknown placeholder: {e}")
        rendered = template

    return rendered


# ─────────────────────────────────────────────────────────────────────────────
# CORE SEND FUNCTION
# Single entry point for all notifications — used by everything.
# ─────────────────────────────────────────────────────────────────────────────

def send_appointment_notification(
    db: Session,
    appointment_id: int,
    notification_type: str,
    template_key: Optional[str] = None,
    custom_template: Optional[str] = None,
    minutes_before: int = 0,
    force: bool = False,
) -> bool:
    """
    Send an SMS notification for a specific appointment.

    This is the single function used by:
        - The scheduler (automatic reminders)
        - appointment_service.py (confirmations, cancellations, reschedules)
        - The AI tool (manual triggers from the AI agent)
        - The notifications API endpoint (dashboard manual sends)

    Duplicate prevention:
        Uses _already_sent() to check if this exact notification was
        already sent. Pass force=True to bypass (for manual resends).

    Template selection priority:
        1. custom_template if provided
        2. template_key if provided
        3. notification_type as a template key fallback

    Args:
        db:                database session
        appointment_id:    which appointment
        notification_type: "reminder", "confirmation", "cancellation", etc.
        template_key:      which template from DEFAULT_TEMPLATES
        custom_template:   override with completely custom message text
        minutes_before:    for reminders — used in duplicate check key
        force:             bypass duplicate check if True

    Returns:
        True if sent successfully, False otherwise
    """

    # ── Load records ──────────────────────────────────────────────────────
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id
    ).first()

    if not appointment:
        logger.warning(f"Notification skipped: appointment {appointment_id} not found")
        return False

    # skip most notifications for already-closed appointments
    if appointment.status in ("cancelled", "no_show") and notification_type not in (
        "cancellation", "no_show_followup", "custom"
    ):
        logger.debug(
            f"Notification skipped: appointment {appointment_id} "
            f"is {appointment.status}, type={notification_type}"
        )
        return False

    agent = db.query(AIAgent).filter(AIAgent.id == appointment.agent_id).first()
    customer = db.query(Customer).filter(Customer.id == appointment.customer_id).first()
    employee = db.query(Employee).filter(Employee.id == appointment.employee_id).first()
    service = (
        db.query(Service).filter(Service.id == appointment.service_id).first()
        if appointment.service_id else None
    )

    if not agent or not customer:
        logger.warning(f"Notification skipped: missing agent or customer for appointment {appointment_id}")
        return False

    # ── Duplicate check ───────────────────────────────────────────────────
    if not force and _already_sent(appointment_id, notification_type, minutes_before):
        logger.debug(
            f"Notification skipped (already sent): appointment={appointment_id} "
            f"type={notification_type} minutes_before={minutes_before}"
        )
        return False

    # ── Select template ───────────────────────────────────────────────────
    effective_template_key = template_key or notification_type

    # ── Render ────────────────────────────────────────────────────────────
    message = render_template(
        template_key=effective_template_key,
        appointment=appointment,
        agent=agent,
        customer=customer,
        employee=employee,
        service=service,
        custom_template=custom_template,
    )

    if not message:
        logger.error(
            f"Notification skipped: empty message for type='{notification_type}' "
            f"template='{effective_template_key}'"
        )
        return False

    # ── Send ──────────────────────────────────────────────────────────────
    sent = send_sms(
        to=customer.phone,
        body=message,
        from_number=agent.phone_number,
    )

    if sent:
        _mark_sent(appointment_id, notification_type, minutes_before)
        logger.info(
            f"Notification sent: type={notification_type} "
            f"appointment={appointment_id} to={customer.phone} "
            f"preview='{message[:80]}'"
        )
    else:
        logger.error(
            f"Notification FAILED: type={notification_type} "
            f"appointment={appointment_id} to={customer.phone}"
        )

    return sent


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER JOB — runs every minute via APScheduler
# ─────────────────────────────────────────────────────────────────────────────

def run_reminder_check(db: Session) -> dict:
    """
    Scheduler job — checks the database every minute for due reminders.

    For each active agent and each enabled notification rule:
        1. Calculates the target time window (appointments starting in
           exactly minutes_before ± 1 minute from now)
        2. Queries appointments in that window
        3. Sends reminder if not already sent

    The ± 1 minute window handles scheduler imprecision gracefully.
    The _already_sent check ensures no appointment gets double-notified
    even if the scheduler runs slightly off schedule.

    No LLM involved. Pure SQL queries + template rendering + Twilio.

    Args:
        db: database session (injected by the scheduler setup in main.py)

    Returns:
        dict with sent and checked counts for logging
    """

    now_utc = datetime.now(pytz.UTC)
    sent_count = 0
    checked_count = 0

    logger.debug(f"Reminder check running at {now_utc.strftime('%H:%M:%S UTC')}")

    agents = db.query(AIAgent).filter(AIAgent.active == True).all()

    for agent in agents:
        for rule in DEFAULT_NOTIFICATION_RULES:

            if not rule.get("enabled", True):
                continue

            if rule["type"] != "reminder":
                continue

            minutes_before = rule["minutes_before"]
            template_key = rule["template_key"]

            # target window: appointments whose start time is exactly
            # minutes_before minutes from now, within ± 1 minute tolerance
            window_start = now_utc + timedelta(minutes=minutes_before - 1)
            window_end = now_utc + timedelta(minutes=minutes_before + 1)

            due_appointments = db.query(Appointment).filter(
                Appointment.agent_id == agent.id,
                Appointment.start_time >= window_start,
                Appointment.start_time <= window_end,
                Appointment.status.in_(["confirmed", "pending"]),
            ).all()

            for appointment in due_appointments:
                checked_count += 1
                sent = send_appointment_notification(
                    db=db,
                    appointment_id=appointment.id,
                    notification_type="reminder",
                    template_key=template_key,
                    minutes_before=minutes_before,
                )
                if sent:
                    sent_count += 1

    result = {
        "checked": checked_count,
        "sent": sent_count,
        "timestamp": now_utc.isoformat(),
    }

    if sent_count > 0:
        logger.info(
            f"Reminders sent: {sent_count} sent, {checked_count} checked"
        )
    else:
        logger.debug(
            f"Reminder check done: 0 due (checked {checked_count} appointments)"
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE WRAPPERS
# Called directly from appointment_service.py at lifecycle events.
# Each wraps send_appointment_notification with the right defaults.
# ─────────────────────────────────────────────────────────────────────────────

def send_confirmation(db: Session, appointment_id: int) -> bool:
    """Send booking confirmation immediately after appointment creation."""
    return send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="confirmation",
        template_key="confirmation",
    )


def send_cancellation(db: Session, appointment_id: int) -> bool:
    """Send cancellation notice when appointment is cancelled."""
    return send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="cancellation",
        template_key="cancellation",
        force=True,  # always send even if confirmation was previously sent
    )


def send_reschedule(db: Session, appointment_id: int) -> bool:
    """Send reschedule notice when appointment time or employee changes."""
    return send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="reschedule",
        template_key="reschedule",
        force=True,
    )


def send_no_show_followup(db: Session, appointment_id: int) -> bool:
    """Send follow-up SMS to customers who missed their appointment."""
    return send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="no_show_followup",
        template_key="no_show_followup",
    )


def send_custom(db: Session, appointment_id: int, message: str) -> bool:
    """Send a completely custom message for any appointment."""
    return send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="custom",
        custom_template=message,
        force=True,
    )