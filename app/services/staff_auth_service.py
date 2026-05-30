"""
app/services/staff_auth_service.py

The "own scope" dashboard surface for users linked to an employee record.

Authentication and invitations now live in auth_service / user_service (single
unified User identity). What remains here is the read-only calendar context plus
own-appointment actions that back the staff dashboard — every function resolves
the branch + employee from the authenticated CurrentUser, never the request
body, so a user can only ever see their own data.
"""

from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.dependencies import CurrentUser
from app.core.exceptions import ForbiddenException, NotFoundException
from app.models.models import (
    AIAgent,
    Appointment,
    AppointmentStatusEnum,
    Customer,
    DayOff,
    Employee,
    EmployeeBranch,
    Service,
)


# ─────────────────────────────────────────────────────────────────────────────
# SCOPE
# ─────────────────────────────────────────────────────────────────────────────

def _staff_scope(
    db: Session,
    current: CurrentUser,
    agent_id: Optional[int] = None,
) -> tuple[Employee, AIAgent]:
    """
    Resolve the (employee, branch) the current user owns.

    The user is linked to a single Employee (1:1) but that Employee may belong
    to several branches and the user may hold dashboard access at a subset of
    them.

    - When `agent_id` is provided (the staff-dashboard branch picker), validate
      that it belongs to the intersection of the employee's `EmployeeBranch`
      rows AND the user's `UserBranchRole`s. A spoofed id from outside that
      intersection raises `NO_BRANCH_ACCESS`.
    - When omitted, deterministically pick the "primary" branch — the smallest
      `agent_id` in the intersection — so the staff calendar always lands on
      the same branch across logins.
    """
    if current.employee_id is None:
        raise ForbiddenException(
            detail="This view is only available to employee dashboard accounts",
            code="NOT_AN_EMPLOYEE",
        )
    emp = db.query(Employee).filter(Employee.id == current.employee_id).first()
    if not emp:
        raise NotFoundException(detail="Employee not found")

    employee_branch_ids = {
        row.agent_id
        for row in (
            db.query(EmployeeBranch.agent_id)
            .filter(EmployeeBranch.employee_id == emp.id)
            .all()
        )
    }
    accessible = employee_branch_ids & set(current.accessible_agent_ids)
    if not accessible:
        # The user is linked to an Employee, but their dashboard-role
        # assignments no longer cover any of that Employee's branches.
        raise ForbiddenException(
            detail="You no longer have dashboard access to this employee's branches",
            code="NO_BRANCH_ACCESS",
        )
    if agent_id is not None:
        if agent_id not in accessible:
            raise ForbiddenException(
                detail="You do not have access to that branch",
                code="NO_BRANCH_ACCESS",
            )
        resolved_id = agent_id
    else:
        resolved_id = min(accessible)
    agent = db.query(AIAgent).filter(AIAgent.id == resolved_id).first()
    if not agent:
        raise NotFoundException(detail="Branch not found")
    return emp, agent


def _staff_accessible_branches(db: Session, current: CurrentUser) -> list[dict]:
    """
    Return every branch the staff user can switch to — the intersection of
    their Employee's `EmployeeBranch` rows and their `UserBranchRole`s, sorted
    by `agent_id`. Each item also surfaces the per-branch `active` flag so the
    picker UI can mark "paused at X" entries.
    """
    if current.employee_id is None:
        return []
    rows = (
        db.query(EmployeeBranch, AIAgent)
        .join(AIAgent, AIAgent.id == EmployeeBranch.agent_id)
        .filter(EmployeeBranch.employee_id == current.employee_id)
        .filter(EmployeeBranch.agent_id.in_(current.accessible_agent_ids))
        .order_by(AIAgent.id)
        .all()
    )
    return [
        {
            "agent_id": agent.id,
            "agent_name": agent.agent_name,
            "active": bool(branch.active),
        }
        for branch, agent in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ME
# ─────────────────────────────────────────────────────────────────────────────

def get_staff_me(
    db: Session,
    current: CurrentUser,
    agent_id: Optional[int] = None,
) -> dict:
    emp, agent = _staff_scope(db, current, agent_id=agent_id)
    # Surface the per-branch active flag for the resolved branch so the staff
    # dashboard reflects "you're paused at this branch" correctly (and
    # downstream UI gating can react without a second round-trip).
    branch_row = (
        db.query(EmployeeBranch)
        .filter(
            EmployeeBranch.employee_id == emp.id,
            EmployeeBranch.agent_id == agent.id,
        )
        .first()
    )
    branch_active = bool(branch_row.active) if branch_row else True
    return {
        "id": current.id,
        "employee_id": emp.id,
        "email": current.email,
        "active": branch_active,
        "employee_name": emp.name,
        "profile_picture_url": emp.profile_picture_url,
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "timezone": agent.timezone,
        "business_id": agent.business_id,
        "business_name": agent.business.name,
        # Every branch the staff user can switch to (intersection of their
        # Employee's branches and their UserBranchRoles). The picker uses this
        # to render the topbar dropdown; calls to other /staff/* endpoints
        # then thread the chosen agent_id back as a query param.
        "branches": _staff_accessible_branches(db, current),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CALENDAR CONTEXT (read-only, own branch + own schedule)
# ─────────────────────────────────────────────────────────────────────────────

def get_staff_business_hours(
    db: Session, current: CurrentUser, agent_id: Optional[int] = None
) -> dict:
    from app.services import business_hours_service
    _, agent = _staff_scope(db, current, agent_id=agent_id)
    return business_hours_service.get_business_hours(db, agent.id, agent.business_id)


def get_staff_working_hours(
    db: Session, current: CurrentUser, agent_id: Optional[int] = None
) -> dict:
    from app.services import employee_service
    emp, agent = _staff_scope(db, current, agent_id=agent_id)
    return employee_service.get_working_hours(db, emp.id, agent.id, agent.business_id)


def get_staff_blocked_times(
    db: Session, current: CurrentUser, agent_id: Optional[int] = None
) -> dict:
    from app.services import employee_service
    emp, agent = _staff_scope(db, current, agent_id=agent_id)
    return employee_service.get_blocked_times(db, emp.id, agent.id, agent.business_id)


def get_staff_days_off(
    db: Session, current: CurrentUser, agent_id: Optional[int] = None
) -> dict:
    """Branch-wide closures (employee_id IS NULL) plus this user's personal days off."""
    emp, agent = _staff_scope(db, current, agent_id=agent_id)
    rows = (
        db.query(DayOff)
        .filter(
            DayOff.agent_id == agent.id,
            (DayOff.employee_id == None) | (DayOff.employee_id == emp.id),  # noqa: E711
        )
        .order_by(DayOff.start_date)
        .all()
    )
    return {"days_off": rows, "total": len(rows)}


def get_staff_agent_time(
    db: Session, current: CurrentUser, agent_id: Optional[int] = None
) -> dict:
    from app.services import availability_service
    _, agent = _staff_scope(db, current, agent_id=agent_id)
    return availability_service.get_agent_time(db, agent.id, agent.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# APPOINTMENTS (own only)
# ─────────────────────────────────────────────────────────────────────────────

def get_staff_appointments(
    db: Session,
    current: CurrentUser,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> dict:
    from app.services.appointment_service import _build_detail

    emp, agent = _staff_scope(db, current, agent_id=agent_id)

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
    current: CurrentUser,
    appointment_id: int,
    status: AppointmentStatusEnum,
) -> dict:
    from app.services.appointment_service import _build_detail

    # Resolve the branch from the target appointment so a status change works
    # regardless of which branch the user is currently viewing in the picker.
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise NotFoundException(detail="Appointment not found")
    emp, agent = _staff_scope(db, current, agent_id=appointment.agent_id)
    current.require(rbac.P_UPDATE_APPT_STATUS, agent_id=agent.id)

    if appointment.employee_id != emp.id:
        raise ForbiddenException(detail="You do not have access to this appointment")

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
    logger.info(f"User id={current.id} set appointment {appointment_id} → {status}")
    return _build_detail(appointment, agent, customer, emp, service)
