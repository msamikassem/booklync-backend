# app/api/v1/endpoints/notifications.py

"""
Notification management endpoints.
Allows triggering and managing notifications from the dashboard.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
from app.services import appointment_service
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
    current: CurrentUser = Depends(get_current_user),
):
    """Manually resend a booking confirmation."""
    agent_id = appointment_service.resolve_appointment_agent_id(db, appointment_id, current.business_id)
    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent_id)
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
    current: CurrentUser = Depends(get_current_user),
):
    """Manually send a cancellation notice."""
    agent_id = appointment_service.resolve_appointment_agent_id(db, appointment_id, current.business_id)
    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent_id)
    sent = send_cancellation(db, appointment_id)
    return {"sent": sent, "appointment_id": appointment_id, "type": "cancellation"}


@router.post("/send/reminder/{appointment_id}")
def trigger_reminder(
    appointment_id: int,
    data: ManualReminderRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Manually trigger a reminder for an appointment.
    Use template_key to pick which template, or provide custom_message.
    """
    agent_id = appointment_service.resolve_appointment_agent_id(db, appointment_id, current.business_id)
    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent_id)
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
    current: CurrentUser = Depends(get_current_user),
):
    """Send a completely custom SMS message for any appointment."""
    agent_id = appointment_service.resolve_appointment_agent_id(db, data.appointment_id, current.business_id)
    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent_id)
    sent = send_custom(db, data.appointment_id, data.message)
    return {"sent": sent, "appointment_id": data.appointment_id, "type": "custom"}


@router.get("/templates")
def get_templates(
    current: CurrentUser = Depends(get_current_user),
):
    """List all available notification templates."""
    current.require(rbac.P_MESSAGE_CUSTOMERS)
    return {
        "templates": [
            {"key": k, "preview": v[:100] + "..."}
            for k, v in DEFAULT_TEMPLATES.items()
        ]
    }


@router.get("/rules")
def get_rules(
    current: CurrentUser = Depends(get_current_user),
):
    """List all active notification rules (reminder windows)."""
    current.require(rbac.P_MESSAGE_CUSTOMERS)
    return {"rules": DEFAULT_NOTIFICATION_RULES}


@router.post("/scheduler/run")
def run_scheduler_now(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Manually trigger a reminder check right now.
    Useful for testing — simulates what the scheduler does every minute.
    """
    current.require(rbac.P_MANAGE_AUTOMATIONS)
    result = run_reminder_check(db)
    return result