"""
app/services/staff_auth_service.py

Business logic for the staff invite system and staff authentication.

Flow:
  1. Manager sends invite  → EmployeeInvite row created, email sent
  2. Employee clicks link  → AcceptInvite creates StaffAccount, marks invite accepted
  3. Employee logs in      → StaffAccount verified, staff JWT issued
  4. Staff makes requests  → get_current_staff_account dep loads StaffAccount from DB
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    AppException,
)
from app.core.security import (
    create_staff_access_token,
    create_staff_refresh_token,
    hash_password,
    verify_password,
    decode_token,
)
from app.models.models import (
    AIAgent,
    Appointment,
    AppointmentStatusEnum,
    Customer,
    Employee,
    EmployeeInvite,
    InviteStatusEnum,
    Service,
    StaffAccount,
)
from app.utils.email_utils import send_invite_email


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _verify_employee_ownership(db: Session, employee_id: int, business_id: int) -> Employee:
    """Load employee and verify it belongs to the business via its agent."""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found", code="NOT_FOUND")
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()
    if not agent or agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this employee")
    return emp


def _build_staff_tokens(staff: StaffAccount, db: Session) -> dict:
    access_token = create_staff_access_token(staff.id)
    refresh_token = create_staff_refresh_token(staff.id)
    staff.refresh_token = hash_password(refresh_token)
    db.commit()
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# INVITE
# ─────────────────────────────────────────────────────────────────────────────

def send_invite(
    db: Session,
    business_id: int,
    employee_id: int,
    email: Optional[str] = None,
) -> dict:
    """
    Send a dashboard invite to an employee.

    Idempotent: if a non-expired pending invite already exists, reuses that
    token and resends the email rather than creating a duplicate.
    """
    emp = _verify_employee_ownership(db, employee_id, business_id)

    # Determine email to use
    target_email = email or emp.email
    if not target_email:
        raise AppException(
            detail="Employee has no email address. Provide one in the request body.",
            code="EMAIL_REQUIRED",
            status_code=422,
        )

    # If email provided and differs from employee record, update it
    if email and email != emp.email:
        emp.email = email

    # Reject if already has a dashboard account
    if emp.staff_account:
        raise ConflictException(
            detail="This employee already has a dashboard account",
            code="STAFF_ACCOUNT_EXISTS",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.INVITE_EXPIRE_HOURS)

    # Check for a live pending invite — reuse it
    existing = (
        db.query(EmployeeInvite)
        .filter(
            EmployeeInvite.employee_id == employee_id,
            EmployeeInvite.status == InviteStatusEnum.pending,
            EmployeeInvite.expires_at > now,
        )
        .first()
    )

    if existing:
        # Reuse the existing invite — generate a fresh raw token mapped to same hash slot
        raw_token = secrets.token_urlsafe(32)
        existing.token_hash = hash_password(raw_token)
        existing.email = target_email
        existing.expires_at = expires_at
        db.commit()
    else:
        raw_token = secrets.token_urlsafe(32)
        invite = EmployeeInvite(
            employee_id=employee_id,
            email=target_email,
            token_hash=hash_password(raw_token),
            status=InviteStatusEnum.pending,
            expires_at=expires_at,
        )
        db.add(invite)
        db.commit()

    # Load business name for the email (agent → business)
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()
    business_name = agent.business.name if agent and agent.business else "Your employer"

    # Send email — commit is already done so a send failure doesn't undo the invite
    send_invite_email(
        to_email=target_email,
        invite_token=raw_token,
        employee_name=emp.name,
        business_name=business_name,
        api_base_url=settings.API_BASE_URL,
    )

    logger.info(f"Invite sent to {target_email} for employee id={employee_id}")
    return {
        "message": "Invitation sent",
        "email": target_email,
        "expires_at": expires_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ACCEPT INVITE
# ─────────────────────────────────────────────────────────────────────────────

def accept_invite(db: Session, token: str, password: str) -> dict:
    """
    Accept a dashboard invite and create a StaffAccount.
    Returns staff tokens so the employee is logged in immediately.
    """
    now = datetime.now(timezone.utc)

    # Find matching invite by scanning pending ones (token is hashed, must verify each)
    candidates = (
        db.query(EmployeeInvite)
        .filter(EmployeeInvite.status == InviteStatusEnum.pending)
        .all()
    )

    invite = None
    for candidate in candidates:
        if verify_password(token, candidate.token_hash):
            invite = candidate
            break

    if not invite:
        raise NotFoundException(
            detail="Invalid or expired invitation link",
            code="INVITE_NOT_FOUND",
        )

    if invite.expires_at.replace(tzinfo=timezone.utc) < now:
        invite.status = InviteStatusEnum.expired
        db.commit()
        raise AppException(
            detail="This invitation has expired. Ask your manager to send a new one.",
            code="INVITE_EXPIRED",
            status_code=410,
        )

    emp = db.query(Employee).filter(Employee.id == invite.employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found")

    if emp.staff_account:
        raise ConflictException(
            detail="A dashboard account already exists for this employee",
            code="STAFF_ACCOUNT_EXISTS",
        )

    staff = StaffAccount(
        employee_id=emp.id,
        email=invite.email,
        password_hash=hash_password(password),
        active=True,
    )
    db.add(staff)
    db.flush()

    invite.status = InviteStatusEnum.accepted
    db.commit()
    db.refresh(staff)

    logger.info(f"Staff account created for employee id={emp.id} (email={invite.email})")
    return _build_staff_tokens(staff, db)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF LOGIN
# ─────────────────────────────────────────────────────────────────────────────

def staff_login(db: Session, email: str, password: str) -> dict:
    staff = db.query(StaffAccount).filter(StaffAccount.email == email).first()

    if not staff or not verify_password(password, staff.password_hash):
        raise UnauthorizedException(
            detail="Invalid email or password",
            code="INVALID_CREDENTIALS",
        )

    if not staff.active:
        raise UnauthorizedException(
            detail="This staff account is inactive",
            code="ACCOUNT_INACTIVE",
        )

    from sqlalchemy.sql import func
    staff.last_login = func.now()
    db.flush()

    tokens = _build_staff_tokens(staff, db)
    logger.info(f"Staff logged in: id={staff.id} email={email}")
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# STAFF REFRESH
# ─────────────────────────────────────────────────────────────────────────────

def refresh_staff_token(db: Session, refresh_token: str) -> dict:
    payload = decode_token(refresh_token)

    if not payload or payload.get("type") != "refresh" or payload.get("account_type") != "staff":
        raise UnauthorizedException(
            detail="Invalid or expired refresh token",
            code="INVALID_REFRESH_TOKEN",
        )

    staff_id = payload.get("sub")
    staff = db.query(StaffAccount).filter(StaffAccount.id == int(staff_id)).first()

    if not staff:
        raise UnauthorizedException(detail="Staff account not found", code="STAFF_NOT_FOUND")

    if not staff.refresh_token or not verify_password(refresh_token, staff.refresh_token):
        raise UnauthorizedException(
            detail="Refresh token has been revoked",
            code="TOKEN_REVOKED",
        )

    access_token = create_staff_access_token(staff.id)
    logger.info(f"Staff access token refreshed: id={staff.id}")
    return {"access_token": access_token, "token_type": "bearer"}


# ─────────────────────────────────────────────────────────────────────────────
# STAFF LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

def staff_logout(db: Session, staff_account_id: int) -> dict:
    staff = db.query(StaffAccount).filter(StaffAccount.id == staff_account_id).first()
    if staff:
        staff.refresh_token = None
        db.commit()
    logger.info(f"Staff logged out: id={staff_account_id}")
    return {"message": "Logged out successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# STAFF ME
# ─────────────────────────────────────────────────────────────────────────────

def get_staff_me(db: Session, staff_account_id: int) -> dict:
    staff = db.query(StaffAccount).filter(StaffAccount.id == staff_account_id).first()
    if not staff:
        raise NotFoundException(detail="Staff account not found")

    emp = staff.employee
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()

    return {
        "id": staff.id,
        "employee_id": emp.id,
        "email": staff.email,
        "active": staff.active,
        "employee_name": emp.name,
        "profile_picture_url": emp.profile_picture_url,
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "timezone": agent.timezone,
        "business_id": agent.business_id,
        "business_name": agent.business.name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STAFF CALENDAR CONTEXT (read-only, scoped to the staff member's own employee/agent)
#
# These delegate to the same service functions the business-owner endpoints use,
# passing the agent_id/business_id resolved from the staff JWT (never the request).
# A staff member can therefore only ever read their own branch + their own schedule.
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_agent(db: Session, staff: StaffAccount) -> AIAgent:
    """Resolve the staff member's branch agent from the DB at request time."""
    agent = db.query(AIAgent).filter(AIAgent.id == staff.employee.agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    return agent


def get_staff_business_hours(db: Session, staff: StaffAccount) -> dict:
    from app.services import business_hours_service
    agent = _resolve_agent(db, staff)
    return business_hours_service.get_business_hours(db, agent.id, agent.business_id)


def get_staff_working_hours(db: Session, staff: StaffAccount) -> dict:
    from app.services import employee_service
    agent = _resolve_agent(db, staff)
    return employee_service.get_working_hours(db, staff.employee_id, agent.id, agent.business_id)


def get_staff_blocked_times(db: Session, staff: StaffAccount) -> dict:
    from app.services import employee_service
    agent = _resolve_agent(db, staff)
    return employee_service.get_blocked_times(db, staff.employee_id, agent.id, agent.business_id)


def get_staff_days_off(db: Session, staff: StaffAccount) -> dict:
    """Branch-wide closures (employee_id IS NULL) plus this staff member's personal days off."""
    from app.models.models import DayOff
    agent = _resolve_agent(db, staff)
    rows = (
        db.query(DayOff)
        .filter(
            DayOff.agent_id == agent.id,
            (DayOff.employee_id == None) | (DayOff.employee_id == staff.employee_id),  # noqa: E711
        )
        .order_by(DayOff.start_date)
        .all()
    )
    return {"days_off": rows, "total": len(rows)}


def get_staff_agent_time(db: Session, staff: StaffAccount) -> dict:
    from app.services import availability_service
    agent = _resolve_agent(db, staff)
    return availability_service.get_agent_time(db, agent.id, agent.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF APPOINTMENTS
# ─────────────────────────────────────────────────────────────────────────────

def get_staff_appointments(
    db: Session,
    staff: StaffAccount,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
) -> dict:
    """Return only the appointments assigned to this staff member."""
    from app.services.appointment_service import _build_detail, _get_tz

    emp = staff.employee
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()

    query = db.query(Appointment).filter(
        Appointment.employee_id == emp.id,
        Appointment.agent_id == agent.id,
    )
    if date_from:
        query = query.filter(Appointment.start_time >= date_from)
    if date_to:
        query = query.filter(Appointment.start_time <= date_to)
    if status:
        query = query.filter(Appointment.status == status)

    appointments = query.order_by(Appointment.start_time).all()

    details = []
    for appt in appointments:
        customer = db.query(Customer).filter(Customer.id == appt.customer_id).first()
        service = db.query(Service).filter(Service.id == appt.service_id).first() if appt.service_id else None
        details.append(_build_detail(appt, agent, customer, emp, service))

    return {"appointments": details, "total": len(details)}


def update_staff_appointment_status(
    db: Session,
    appointment_id: int,
    status: AppointmentStatusEnum,
    staff: StaffAccount,
) -> dict:
    """Update status of an appointment — only if it belongs to this staff member."""
    from app.services.appointment_service import _build_detail

    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise NotFoundException(detail="Appointment not found")

    if appointment.employee_id != staff.employee_id:
        raise ForbiddenException(detail="You do not have access to this appointment")

    emp = staff.employee
    agent = db.query(AIAgent).filter(AIAgent.id == emp.agent_id).first()

    appointment.status = status
    db.commit()
    db.refresh(appointment)

    if status == AppointmentStatusEnum.no_show:
        try:
            from app.services.notification_service import send_no_show_followup
            send_no_show_followup(db, appointment.id)
        except Exception as e:
            logger.warning(f"No-show SMS failed for appointment {appointment_id}: {e}")

    customer = db.query(Customer).filter(Customer.id == appointment.customer_id).first()
    service = db.query(Service).filter(Service.id == appointment.service_id).first() if appointment.service_id else None
    logger.info(f"Staff id={staff.id} set appointment {appointment_id} → {status}")
    return _build_detail(appointment, agent, customer, emp, service)
