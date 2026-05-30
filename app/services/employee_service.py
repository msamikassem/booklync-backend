"""
app/services/employee_service.py

Business logic for employee management. With multi-branch support:

  - An Employee is a person (name/role/phone/email/profile picture).
  - Membership in a branch is one row in the `employee_branches` join table
    carrying per-branch `active` and `can_receive_bookings` flags.
  - Working hours and recurring breaks are per (employee, branch) so the
    same person can hold different schedules at different branches.

Ownership chain enforced on every operation:
    business → agent → employee_branch (membership) → employee

Helpers below resolve and verify each step. Cross-business / cross-branch
access raises ForbiddenException.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload
from loguru import logger

from app.models.models import (
    Employee,
    EmployeeBranch,
    AIAgent,
    EmployeeWorkingHour,
    BlockedTime,
    DayOff,
    EmployeeCapability,
    InviteStatusEnum,
    Service,
    ServiceTitle,
    ServiceVariation,
    User,
    UserBranchRole,
    UserInvite,
)
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeUpdate,
    WorkingHoursSet,
    BlockedTimeCreate,
    DayOffCreate,
)
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    ConflictException,
)


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD-ACCESS STATUS
# ─────────────────────────────────────────────────────────────────────────────

def _dashboard_access_for(db: Session, employee: Employee) -> dict:
    """
    Compute the current dashboard-access state for an employee. Returns a
    dict that matches `schemas.employee.DashboardAccessSummary`.

    Priority:
      1. If `employee.user` exists → status='active' (with branch breakdown).
      2. Else look at the latest UserInvite. status='invite_pending' if it's
         still in the live pending window, else 'invite_expired'.
      3. Otherwise status='none'.

    The pending/expired distinction also accounts for an invite whose row is
    still `pending` in the DB but whose `expires_at` has already passed —
    `accept_invite` only flips it to `expired` on the next accept attempt.
    """
    if employee.user is not None:
        u = employee.user
        branches: list[dict] = []
        for ubr in u.branch_roles:
            branches.append({
                "agent_id": ubr.agent_id,
                "agent_name": ubr.agent.agent_name if ubr.agent else "",
                "role_key": ubr.role.key if ubr.role else "",
                "role_name": ubr.role.name if ubr.role else "",
            })
        return {
            "status": "active",
            "user_id": u.id,
            "email": u.email,
            "account_branches": branches,
        }

    # No active user — surface the latest invite (if any).
    invite = (
        db.query(UserInvite)
        .filter(UserInvite.employee_id == employee.id)
        .order_by(UserInvite.created_at.desc())
        .first()
    )
    if invite is None or invite.status == InviteStatusEnum.accepted:
        return {"status": "none"}

    now = datetime.now(timezone.utc)
    expires = invite.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    is_expired = (
        invite.status == InviteStatusEnum.expired
        or (expires is not None and expires < now)
    )
    return {
        "status": "invite_expired" if is_expired else "invite_pending",
        "invite_id": invite.id,
        "email": invite.email,
        "invite_role": invite.role.key if invite.role else None,
        "invite_agent_ids": list(invite.agent_ids or []),
        "invite_expires_at": expires,
    }


def _serialize_employee(db: Session, employee: Employee) -> dict:
    """Build the EmployeeResponse dict including branch list + dashboard access."""
    return {
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "phone": employee.phone,
        "email": employee.email,
        "profile_picture_url": employee.profile_picture_url,
        "created_at": employee.created_at,
        "branches": list(employee.branches or []),
        "dashboard_access": _dashboard_access_for(db, employee),
    }


def _serialize_employees(db: Session, employees: list[Employee]) -> list[dict]:
    return [_serialize_employee(db, e) for e in employees]


# ─────────────────────────────────────────────────────────────────────────────
# OWNERSHIP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")
    return agent


def _get_employee_branch(
    db: Session, employee_id: int, agent_id: int
) -> EmployeeBranch:
    """Load the EmployeeBranch row for (employee, branch) or raise."""
    eb = (
        db.query(EmployeeBranch)
        .filter(
            EmployeeBranch.employee_id == employee_id,
            EmployeeBranch.agent_id == agent_id,
        )
        .first()
    )
    if not eb:
        # Existence of the join row IS the membership; treat missing as
        # forbidden (same as old "wrong branch" check) so we don't leak
        # whether an employee with that id exists in a different branch.
        raise ForbiddenException(detail="This employee does not belong to this branch")
    return eb


def _get_employee_for_agent(
    db: Session, employee_id: int, agent_id: int
) -> Employee:
    """
    Verify the employee is assigned to the given branch and return the
    Employee row. Used as the ownership precheck for every per-branch op.
    """
    _get_employee_branch(db, employee_id, agent_id)
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        # Foreign key keeps this in sync, but be defensive.
        raise NotFoundException(detail="Employee not found")
    return employee


def get_employee_branch(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> EmployeeBranch:
    """Public helper: verify business→agent→branch and return the EmployeeBranch."""
    _get_agent_for_business(db, agent_id, business_id)
    return _get_employee_branch(db, employee_id, agent_id)


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_employee(db: Session, data: EmployeeCreate, business_id: int) -> dict:
    """
    Create an Employee and assign them to every branch in `data.agent_ids`.

    Permission gating (`manage_employees` at every selected branch) happens
    at the endpoint layer; this service only enforces tenant scoping.
    """
    # Verify every selected branch belongs to the caller's business.
    for agent_id in data.agent_ids:
        _get_agent_for_business(db, agent_id, business_id)

    employee = Employee(
        name=data.name,
        role=data.role,
        phone=data.phone,
        email=data.email,
    )
    db.add(employee)
    db.flush()  # populate employee.id before inserting the join rows.

    for agent_id in data.agent_ids:
        db.add(
            EmployeeBranch(
                employee_id=employee.id,
                agent_id=agent_id,
                active=True,
                can_receive_bookings=data.can_receive_bookings,
            )
        )

    db.commit()
    db.refresh(employee)

    logger.info(
        f"Employee created: {employee.name} (id={employee.id}) "
        f"at branches={data.agent_ids}"
    )
    return _serialize_employee(db, employee)


def get_employees(
    db: Session,
    agent_id: int,
    business_id: int,
    bookable_only: bool = False,
) -> dict:
    """List employees assigned to a branch."""
    _get_agent_for_business(db, agent_id, business_id)

    query = (
        db.query(Employee)
        .join(EmployeeBranch, EmployeeBranch.employee_id == Employee.id)
        .filter(EmployeeBranch.agent_id == agent_id)
        # Eager-load the relations needed by _serialize_employee so the list
        # endpoint doesn't fire an N+1 storm computing dashboard_access.
        .options(
            selectinload(Employee.branches).selectinload(EmployeeBranch.agent),
            selectinload(Employee.user)
                .selectinload(User.branch_roles)
                .selectinload(UserBranchRole.agent),
            selectinload(Employee.user)
                .selectinload(User.branch_roles)
                .selectinload(UserBranchRole.role),
            selectinload(Employee.invites).selectinload(UserInvite.role),
        )
    )
    if bookable_only:
        query = query.filter(EmployeeBranch.can_receive_bookings.is_(True))
    employees = query.all()

    return {"employees": _serialize_employees(db, employees), "total": len(employees)}


def get_employee(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    """Get one employee (serialized), scoped to a branch the caller can access."""
    _get_agent_for_business(db, agent_id, business_id)
    employee = _get_employee_for_agent(db, employee_id, agent_id)
    return _serialize_employee(db, employee)


def _get_employee_orm(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> Employee:
    """Internal: same scoping as `get_employee` but returns the ORM object —
    used by service callers that need to mutate the employee directly."""
    _get_agent_for_business(db, agent_id, business_id)
    return _get_employee_for_agent(db, employee_id, agent_id)


def update_employee(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: EmployeeUpdate,
    business_id: int,
) -> dict:
    """
    Update an employee. Personal fields update the Employee row (visible at
    every branch). `can_receive_bookings` updates the per-branch row only.
    """
    _get_agent_for_business(db, agent_id, business_id)
    branch = _get_employee_branch(db, employee_id, agent_id)
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise NotFoundException(detail="Employee not found")

    payload = data.model_dump(exclude_none=True)

    # Per-branch flag pulled out so we set it on EmployeeBranch, not Employee.
    if "can_receive_bookings" in payload:
        branch.can_receive_bookings = payload.pop("can_receive_bookings")

    for field, value in payload.items():
        setattr(employee, field, value)

    db.commit()
    db.refresh(employee)

    logger.info(f"Employee updated: id={employee_id} by business_id={business_id}")
    return _serialize_employee(db, employee)


def set_profile_picture(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
    url: str | None,
) -> dict:
    employee = _get_employee_orm(db, employee_id, agent_id, business_id)
    employee.profile_picture_url = url
    db.commit()
    db.refresh(employee)
    return _serialize_employee(db, employee)


def delete_employee(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    """
    Delete an employee outright. Only allowed when the employee is assigned
    to a single branch — multi-branch employees must be unassigned per
    branch first (use `unassign_branch`) so a delete from one branch can't
    accidentally drop them from another the caller may not even own.
    """
    _get_agent_for_business(db, agent_id, business_id)
    _get_employee_branch(db, employee_id, agent_id)

    branch_count = (
        db.query(EmployeeBranch)
        .filter(EmployeeBranch.employee_id == employee_id)
        .count()
    )
    if branch_count > 1:
        raise ConflictException(
            detail=(
                "This employee is assigned to multiple branches. Remove them "
                "from each branch individually before deleting the record."
            ),
            code="EMPLOYEE_MULTI_BRANCH",
        )

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise NotFoundException(detail="Employee not found")
    name = employee.name
    # If a dashboard login is linked, delete it in the same transaction —
    # `User.branch_roles` cascades, so all UserBranchRole rows go with it.
    # The owner is warned about this in the FE confirm dialog before calling.
    linked_user = employee.user
    if linked_user is not None:
        db.delete(linked_user)
    db.delete(employee)
    db.commit()

    logger.info(
        f"Employee deleted: id={employee_id} by business_id={business_id} "
        f"(linked_user_deleted={linked_user is not None})"
    )
    return {"message": f"Employee '{name}' deleted successfully"}


def activate_employee(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    _get_agent_for_business(db, agent_id, business_id)
    branch = _get_employee_branch(db, employee_id, agent_id)
    branch.active = True
    db.commit()
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    logger.info(f"Employee activated at branch: emp={employee_id} agent={agent_id}")
    return _serialize_employee(db, employee)


def deactivate_employee(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    _get_agent_for_business(db, agent_id, business_id)
    branch = _get_employee_branch(db, employee_id, agent_id)
    branch.active = False
    db.commit()
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    logger.info(f"Employee deactivated at branch: emp={employee_id} agent={agent_id}")
    return _serialize_employee(db, employee)


# ─────────────────────────────────────────────────────────────────────────────
# BRANCH ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def assign_branch(
    db: Session,
    employee_id: int,
    new_agent_id: int,
    business_id: int,
    can_receive_bookings: bool = True,
) -> dict:
    """
    Add an existing employee to an additional branch. Idempotent — re-adding
    raises 409 ALREADY_ASSIGNED.
    """
    _get_agent_for_business(db, new_agent_id, business_id)

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise NotFoundException(detail="Employee not found")
    # Verify the employee belongs to the caller's business (some branch).
    if not _employee_belongs_to_business(db, employee_id, business_id):
        raise ForbiddenException(detail="You do not have access to this employee")

    existing = (
        db.query(EmployeeBranch)
        .filter(
            EmployeeBranch.employee_id == employee_id,
            EmployeeBranch.agent_id == new_agent_id,
        )
        .first()
    )
    if existing:
        raise ConflictException(
            detail="Employee is already assigned to this branch",
            code="ALREADY_ASSIGNED",
        )

    db.add(
        EmployeeBranch(
            employee_id=employee_id,
            agent_id=new_agent_id,
            active=True,
            can_receive_bookings=can_receive_bookings,
        )
    )
    db.commit()
    db.refresh(employee)

    logger.info(f"Employee {employee_id} assigned to branch {new_agent_id}")
    return _serialize_employee(db, employee)


def unassign_branch(
    db: Session, employee_id: int, target_agent_id: int, business_id: int
) -> dict:
    """
    Remove an employee from one branch. Cascades:
      - The EmployeeBranch row.
      - Per-branch EmployeeWorkingHour + BlockedTime rows for that agent.
      - EmployeeCapability rows whose target Service / ServiceTitle /
        ServiceVariation lives in that agent (capabilities are naturally
        branch-scoped via the service FK).
      - Personal DayOff rows scoped to that branch.

    Historical Appointment rows are NOT deleted (they're audit records and
    keep their employee_id + agent_id even after unassignment).

    Refuses to remove the employee's last remaining branch — call
    `delete_employee` instead.
    """
    _get_agent_for_business(db, target_agent_id, business_id)
    _get_employee_branch(db, employee_id, target_agent_id)

    branch_count = (
        db.query(EmployeeBranch)
        .filter(EmployeeBranch.employee_id == employee_id)
        .count()
    )
    if branch_count <= 1:
        raise ConflictException(
            detail=(
                "This is the employee's only branch. Delete the employee "
                "instead of unassigning their last branch."
            ),
            code="LAST_BRANCH",
        )

    # Per-branch schedule rows.
    db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee_id,
        EmployeeWorkingHour.agent_id == target_agent_id,
    ).delete(synchronize_session=False)
    db.query(BlockedTime).filter(
        BlockedTime.employee_id == employee_id,
        BlockedTime.agent_id == target_agent_id,
    ).delete(synchronize_session=False)
    db.query(DayOff).filter(
        DayOff.employee_id == employee_id,
        DayOff.agent_id == target_agent_id,
    ).delete(synchronize_session=False)

    # Capabilities whose target lives in this branch.
    # Title-level
    title_cap_ids = [
        c.id
        for c in (
            db.query(EmployeeCapability)
            .join(ServiceTitle, ServiceTitle.id == EmployeeCapability.title_id)
            .filter(
                EmployeeCapability.employee_id == employee_id,
                ServiceTitle.agent_id == target_agent_id,
            )
            .all()
        )
    ]
    # Service-level
    service_cap_ids = [
        c.id
        for c in (
            db.query(EmployeeCapability)
            .join(Service, Service.id == EmployeeCapability.service_id)
            .filter(
                EmployeeCapability.employee_id == employee_id,
                Service.agent_id == target_agent_id,
            )
            .all()
        )
    ]
    # Variation-level (joined via the variation's parent service)
    variation_cap_ids = [
        c.id
        for c in (
            db.query(EmployeeCapability)
            .join(
                ServiceVariation,
                ServiceVariation.id == EmployeeCapability.variation_id,
            )
            .join(Service, Service.id == ServiceVariation.service_id)
            .filter(
                EmployeeCapability.employee_id == employee_id,
                Service.agent_id == target_agent_id,
            )
            .all()
        )
    ]
    cap_ids = set(title_cap_ids + service_cap_ids + variation_cap_ids)
    if cap_ids:
        db.query(EmployeeCapability).filter(
            EmployeeCapability.id.in_(cap_ids)
        ).delete(synchronize_session=False)

    # Dashboard access at this branch must follow employment. If a User is
    # linked to this employee and holds a UserBranchRole at the target branch,
    # delete that row — otherwise we'd leave an orphan login that can still
    # see/manage a branch the person no longer works at. The User row + any
    # remaining UserBranchRole rows at OTHER branches are preserved. If this
    # was the user's last branch role, they survive as a no-access account
    # (use DELETE /employees/{id}/account to remove the login entirely).
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    user_branch_role_removed = False
    if employee is not None and employee.user is not None:
        removed = (
            db.query(UserBranchRole)
            .filter(
                UserBranchRole.user_id == employee.user.id,
                UserBranchRole.agent_id == target_agent_id,
            )
            .delete(synchronize_session=False)
        )
        user_branch_role_removed = bool(removed)

    # Finally the membership row.
    db.query(EmployeeBranch).filter(
        EmployeeBranch.employee_id == employee_id,
        EmployeeBranch.agent_id == target_agent_id,
    ).delete(synchronize_session=False)

    db.commit()

    logger.info(
        f"Employee {employee_id} unassigned from branch {target_agent_id} "
        f"(user_branch_role_removed={user_branch_role_removed})"
    )
    return {"message": "Employee removed from branch successfully"}


def _employee_belongs_to_business(
    db: Session, employee_id: int, business_id: int
) -> bool:
    """True iff the employee has at least one branch in the given business."""
    return (
        db.query(EmployeeBranch)
        .join(AIAgent, AIAgent.id == EmployeeBranch.agent_id)
        .filter(
            EmployeeBranch.employee_id == employee_id,
            AIAgent.business_id == business_id,
        )
        .first()
        is not None
    )


# ─────────────────────────────────────────────────────────────────────────────
# WORKING HOURS (per branch)
# ─────────────────────────────────────────────────────────────────────────────

def set_working_hours(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: WorkingHoursSet,
    business_id: int,
) -> dict:
    """
    Replace the weekly schedule for (employee, branch). Other branches'
    schedules are untouched.
    """
    get_employee(db, employee_id, agent_id, business_id)

    db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee_id,
        EmployeeWorkingHour.agent_id == agent_id,
    ).delete(synchronize_session=False)

    new_hours = []
    for wh in data.working_hours:
        working_hour = EmployeeWorkingHour(
            employee_id=employee_id,
            agent_id=agent_id,
            day_of_week=wh.day_of_week,
            start_time=wh.start_time,
            end_time=wh.end_time,
        )
        db.add(working_hour)
        new_hours.append(working_hour)

    db.commit()
    for wh in new_hours:
        db.refresh(wh)

    logger.info(
        f"Working hours set for employee_id={employee_id} agent_id={agent_id}: "
        f"{len(new_hours)} days"
    )
    return {"working_hours": new_hours, "total": len(new_hours)}


def get_working_hours(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    hours = (
        db.query(EmployeeWorkingHour)
        .filter(
            EmployeeWorkingHour.employee_id == employee_id,
            EmployeeWorkingHour.agent_id == agent_id,
        )
        .order_by(EmployeeWorkingHour.day_of_week)
        .all()
    )
    return {"working_hours": hours, "total": len(hours)}


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED TIMES (per branch)
# ─────────────────────────────────────────────────────────────────────────────

def add_blocked_time(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: BlockedTimeCreate,
    business_id: int,
) -> BlockedTime:
    get_employee(db, employee_id, agent_id, business_id)
    blocked = BlockedTime(
        employee_id=employee_id,
        agent_id=agent_id,
        day_of_week=data.day_of_week,
        start_time=data.start_time,
        end_time=data.end_time,
        reason=data.reason,
    )
    db.add(blocked)
    db.commit()
    db.refresh(blocked)
    logger.info(
        f"Blocked time added: emp={employee_id} agent={agent_id} "
        f"dow={data.day_of_week} {data.start_time}–{data.end_time}"
    )
    return blocked


def get_blocked_times(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    blocked_times = (
        db.query(BlockedTime)
        .filter(
            BlockedTime.employee_id == employee_id,
            BlockedTime.agent_id == agent_id,
        )
        .order_by(BlockedTime.day_of_week)
        .all()
    )
    return {"blocked_times": blocked_times, "total": len(blocked_times)}


def delete_blocked_time(
    db: Session,
    employee_id: int,
    agent_id: int,
    blocked_time_id: int,
    business_id: int,
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    blocked = (
        db.query(BlockedTime)
        .filter(
            BlockedTime.id == blocked_time_id,
            BlockedTime.employee_id == employee_id,
            BlockedTime.agent_id == agent_id,
        )
        .first()
    )
    if not blocked:
        raise NotFoundException(detail="Blocked time not found")

    db.delete(blocked)
    db.commit()
    logger.info(f"Blocked time deleted: id={blocked_time_id} emp={employee_id}")
    return {"message": "Blocked time removed successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# PERSONAL DAYS OFF
# ─────────────────────────────────────────────────────────────────────────────

def add_day_off(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: DayOffCreate,
    business_id: int,
) -> DayOff:
    get_employee(db, employee_id, agent_id, business_id)
    day_off = DayOff(
        agent_id=agent_id,
        employee_id=employee_id,
        name=data.name,
        start_date=data.start_date,
        end_date=data.end_date,
    )
    db.add(day_off)
    db.commit()
    db.refresh(day_off)
    logger.info(
        f"Day off added: emp={employee_id} agent={agent_id} "
        f"{data.start_date}→{data.end_date}"
    )
    return day_off


def get_days_off(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    days_off = (
        db.query(DayOff)
        .filter(
            DayOff.employee_id == employee_id,
            DayOff.agent_id == agent_id,
        )
        .order_by(DayOff.start_date)
        .all()
    )
    return {"days_off": days_off, "total": len(days_off)}


def delete_day_off(
    db: Session,
    employee_id: int,
    agent_id: int,
    day_off_id: int,
    business_id: int,
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    day_off = (
        db.query(DayOff)
        .filter(
            DayOff.id == day_off_id,
            DayOff.employee_id == employee_id,
            DayOff.agent_id == agent_id,
        )
        .first()
    )
    if not day_off:
        raise NotFoundException(detail="Day off not found")
    db.delete(day_off)
    db.commit()
    logger.info(f"Day off deleted: id={day_off_id} emp={employee_id}")
    return {"message": "Day off removed successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CAPABILITIES
# ─────────────────────────────────────────────────────────────────────────────

def get_capabilities(
    db: Session, employee_id: int, agent_id: int, business_id: int
) -> list:
    """
    List capabilities for an employee, filtered to targets that live in the
    given branch. Capabilities are naturally per-branch because their FK
    target (title/service/variation) belongs to exactly one branch.
    """
    get_employee(db, employee_id, agent_id, business_id)

    # Title-level capabilities for titles in this branch.
    title_caps = (
        db.query(EmployeeCapability)
        .join(ServiceTitle, ServiceTitle.id == EmployeeCapability.title_id)
        .filter(
            EmployeeCapability.employee_id == employee_id,
            ServiceTitle.agent_id == agent_id,
        )
        .all()
    )
    # Service-level capabilities for services in this branch.
    service_caps = (
        db.query(EmployeeCapability)
        .join(Service, Service.id == EmployeeCapability.service_id)
        .filter(
            EmployeeCapability.employee_id == employee_id,
            Service.agent_id == agent_id,
        )
        .all()
    )
    # Variation-level capabilities for variations whose service is in this branch.
    variation_caps = (
        db.query(EmployeeCapability)
        .join(
            ServiceVariation,
            ServiceVariation.id == EmployeeCapability.variation_id,
        )
        .join(Service, Service.id == ServiceVariation.service_id)
        .filter(
            EmployeeCapability.employee_id == employee_id,
            Service.agent_id == agent_id,
        )
        .all()
    )
    return title_caps + service_caps + variation_caps


def add_capability(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
    title_id: int | None = None,
    service_id: int | None = None,
    variation_id: int | None = None,
    extra_price=None,
) -> EmployeeCapability:
    """
    Add a capability for an employee. The target must belong to the branch
    the request is scoped to, and the employee must be bookable at that
    branch (per-branch can_receive_bookings).
    """
    _get_agent_for_business(db, agent_id, business_id)
    branch = _get_employee_branch(db, employee_id, agent_id)

    if not branch.can_receive_bookings:
        raise ConflictException(
            detail="This employee does not receive bookings at this branch and cannot be assigned services",
            code="EMPLOYEE_NOT_BOOKABLE",
        )

    provided = [x for x in (title_id, service_id, variation_id) if x is not None]
    if len(provided) != 1:
        raise ConflictException(
            detail="Provide exactly one of: title_id, service_id, variation_id",
            code="INVALID_CAPABILITY",
        )

    if title_id:
        obj = (
            db.query(ServiceTitle)
            .filter(ServiceTitle.id == title_id, ServiceTitle.agent_id == agent_id)
            .first()
        )
        if not obj:
            raise NotFoundException(detail="Service title not found for this agent")
    elif service_id:
        obj = (
            db.query(Service)
            .filter(Service.id == service_id, Service.agent_id == agent_id)
            .first()
        )
        if not obj:
            raise NotFoundException(detail="Service not found for this agent")
    else:
        obj = (
            db.query(ServiceVariation)
            .join(Service)
            .filter(
                ServiceVariation.id == variation_id,
                Service.agent_id == agent_id,
            )
            .first()
        )
        if not obj:
            raise NotFoundException(detail="Service variation not found for this agent")

    dup_filter = []
    if title_id:
        dup_filter.append(EmployeeCapability.title_id == title_id)
    if service_id:
        dup_filter.append(EmployeeCapability.service_id == service_id)
    if variation_id:
        dup_filter.append(EmployeeCapability.variation_id == variation_id)

    existing = (
        db.query(EmployeeCapability)
        .filter(EmployeeCapability.employee_id == employee_id, or_(*dup_filter))
        .first()
    )
    if existing:
        raise ConflictException(
            detail="Capability already assigned", code="ALREADY_ASSIGNED"
        )

    cap = EmployeeCapability(
        employee_id=employee_id,
        title_id=title_id,
        service_id=service_id,
        variation_id=variation_id,
        extra_price=extra_price if extra_price is not None else Decimal("0"),
    )
    db.add(cap)
    db.commit()
    db.refresh(cap)
    return cap


def remove_capability(
    db: Session,
    employee_id: int,
    agent_id: int,
    capability_id: int,
    business_id: int,
) -> dict:
    get_employee(db, employee_id, agent_id, business_id)
    cap = (
        db.query(EmployeeCapability)
        .filter(
            EmployeeCapability.id == capability_id,
            EmployeeCapability.employee_id == employee_id,
        )
        .first()
    )
    if not cap:
        raise NotFoundException(detail="Capability not found")
    db.delete(cap)
    db.commit()
    return {"message": "Capability removed successfully"}
