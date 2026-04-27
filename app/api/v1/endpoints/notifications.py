# app/api/v1/endpoints/notifications.py

"""
Notification management endpoints.
Allows triggering and managing notifications from the dashboard.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.services.notification_service import (
    send_appointment_notification,
    send_confirmation,
    send_cancellation,
    send_reschedule,
    send_no_show_followup,
    send_custom,
    DEFAULT_TEMPLATES,
    DEFAULT_NOTIFICATION_RULES,
    run_reminder_check,
)
from pydantic import BaseModel

router = APIRouter()


class CustomNotificationRequest(BaseModel):
    appointment_id: int
    message: str


class ManualReminderRequest(BaseModel):
    appointment_id: int
    template_key: Optional[str] = "reminder_custom"
    custom_message: Optional[str] = None
    minutes_before: int = 0


@router.post("/send/confirmation/{appointment_id}")
def trigger_confirmation(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Manually resend a booking confirmation."""
    sent = send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="confirmation",
        template_key="confirmation",
        force=True,    # force resend even if already sent
    )
    return {"sent": sent, "appointment_id": appointment_id, "type": "confirmation"}


@router.post("/send/cancellation/{appointment_id}")
def trigger_cancellation(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Manually send a cancellation notice."""
    sent = send_cancellation(db, appointment_id)
    return {"sent": sent, "appointment_id": appointment_id, "type": "cancellation"}


@router.post("/send/reminder/{appointment_id}")
def trigger_reminder(
    appointment_id: int,
    data: ManualReminderRequest,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Manually trigger a reminder for an appointment.
    Use template_key to pick which template, or provide custom_message.
    """
    sent = send_appointment_notification(
        db=db,
        appointment_id=appointment_id,
        notification_type="reminder",
        template_key=data.template_key,
        custom_template=data.custom_message,
        force=True,
    )
    return {"sent": sent, "appointment_id": appointment_id, "type": "reminder"}


@router.post("/send/custom")
def trigger_custom(
    data: CustomNotificationRequest,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Send a completely custom SMS message for any appointment."""
    sent = send_custom(db, data.appointment_id, data.message)
    return {"sent": sent, "appointment_id": data.appointment_id, "type": "custom"}


@router.get("/templates")
def get_templates(
    current_business: Business = Depends(get_current_business),
):
    """List all available notification templates."""
    return {
        "templates": [
            {"key": k, "preview": v[:100] + "..."}
            for k, v in DEFAULT_TEMPLATES.items()
        ]
    }


@router.get("/rules")
def get_rules(
    current_business: Business = Depends(get_current_business),
):
    """List all active notification rules (reminder windows)."""
    return {"rules": DEFAULT_NOTIFICATION_RULES}


@router.post("/scheduler/run")
def run_scheduler_now(
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Manually trigger a reminder check right now.
    Useful for testing — simulates what the scheduler does every minute.
    """
    result = run_reminder_check(db)
    return result