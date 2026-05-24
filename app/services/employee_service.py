"""
app/services/employee_service.py

All business logic for employee management.

Ownership chain enforced on every operation:
    business → agent → employee

Every function that modifies an employee first verifies:
1. The agent belongs to the logged in business
2. The employee belongs to that agent

This prevents any cross-business data access.
"""

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import (
    Employee,
    AIAgent,
    EmployeeWorkingHour,
    BlockedTime,
    DayOff,
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
# HELPER — verify agent ownership
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """
    Fetch an agent and verify it belongs to the logged in business.
    Used internally before any employee operation.

    Raises:
        NotFoundException:  agent not found
        ForbiddenException: agent belongs to different business
    """
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()

    if not agent:
        raise NotFoundException(detail="Agent not found")

    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    return agent


def _get_employee_for_agent(db: Session, employee_id: int, agent_id: int) -> Employee:
    """
    Fetch an employee and verify it belongs to the given agent.
    Used internally before any employee operation.

    Raises:
        NotFoundException:  employee not found
        ForbiddenException: employee belongs to different agent
    """
    employee = db.query(Employee).filter(Employee.id == employee_id).first()

    if not employee:
        raise NotFoundException(detail="Employee not found")

    if employee.agent_id != agent_id:
        raise ForbiddenException(detail="This employee does not belong to this agent")

    return employee


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_employee(db: Session, data: EmployeeCreate, business_id: int) -> Employee:
    """
    Create a new employee under an agent.

    Steps:
    1. Verify the agent belongs to the logged in business
    2. Create the employee record

    Args:
        db:          database session
        data:        validated EmployeeCreate schema
        business_id: id of the logged in business

    Returns:
        Employee SQLAlchemy object

    Raises:
        NotFoundException:  agent not found
        ForbiddenException: agent belongs to different business
    """

    # Step 1 — verify agent ownership
    _get_agent_for_business(db, data.agent_id, business_id)

    # Step 2 — create the employee
    employee = Employee(
        agent_id=data.agent_id,
        name=data.name,
        role=data.role,
        phone=data.phone,
        email=data.email,
        active=True,     # employees start active by default
    )

    db.add(employee)
    db.commit()
    db.refresh(employee)

    logger.info(f"Employee created: {employee.name} (id={employee.id}) for agent_id={data.agent_id}")

    return employee


def get_employees(db: Session, agent_id: int, business_id: int) -> dict:
    """
    Get all employees for a specific agent.

    Verifies the agent belongs to the logged in business first.

    Args:
        db:          database session
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with employees list and total count
    """

    # verify agent ownership
    _get_agent_for_business(db, agent_id, business_id)

    employees = db.query(Employee).filter(
        Employee.agent_id == agent_id
    ).all()

    return {
        "employees": employees,
        "total": len(employees),
    }


def get_employee(db: Session, employee_id: int, agent_id: int, business_id: int) -> Employee:
    """
    Get one specific employee by id.

    Verifies the full ownership chain:
    business → agent → employee

    Args:
        db:          database session
        employee_id: id of the employee
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        Employee SQLAlchemy object
    """

    # verify agent belongs to business
    _get_agent_for_business(db, agent_id, business_id)

    # verify employee belongs to agent
    employee = _get_employee_for_agent(db, employee_id, agent_id)

    return employee


def update_employee(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: EmployeeUpdate,
    business_id: int,
) -> Employee:
    """
    Update an existing employee.
    Only updates fields that are actually sent.

    Args:
        db:          database session
        employee_id: id of the employee to update
        agent_id:    id of the agent
        data:        validated EmployeeUpdate schema
        business_id: id of the logged in business

    Returns:
        Updated Employee SQLAlchemy object
    """

    employee = get_employee(db, employee_id, agent_id, business_id)

    # update only fields that were sent
    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(employee, field, value)

    db.commit()
    db.refresh(employee)

    logger.info(f"Employee updated: id={employee_id} by business_id={business_id}")

    return employee


def set_profile_picture(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
    url: str | None,
) -> Employee:
    employee = get_employee(db, employee_id, agent_id, business_id)
    employee.profile_picture_url = url
    db.commit()
    db.refresh(employee)
    return employee


def delete_employee(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Delete an employee and all their related data.

    Cascade deletes automatically remove:
    - working hours
    - blocked times
    - personal days off
    - appointments

    Args:
        db:          database session
        employee_id: id of the employee to delete
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with success message
    """

    employee = get_employee(db, employee_id, agent_id, business_id)
    name = employee.name

    db.delete(employee)
    db.commit()

    logger.info(f"Employee deleted: id={employee_id} by business_id={business_id}")

    return {"message": f"Employee '{name}' deleted successfully"}


def activate_employee(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> Employee:
    """Activate an employee so they can receive bookings."""

    employee = get_employee(db, employee_id, agent_id, business_id)
    employee.active = True
    db.commit()
    db.refresh(employee)

    logger.info(f"Employee activated: id={employee_id}")

    return employee


def deactivate_employee(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> Employee:
    """Deactivate an employee so they stop receiving bookings."""

    employee = get_employee(db, employee_id, agent_id, business_id)
    employee.active = False
    db.commit()
    db.refresh(employee)

    logger.info(f"Employee deactivated: id={employee_id}")

    return employee


# ─────────────────────────────────────────────────────────────────────────────
# WORKING HOURS
# ─────────────────────────────────────────────────────────────────────────────

def set_working_hours(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: WorkingHoursSet,
    business_id: int,
) -> dict:
    """
    Set the weekly working schedule for an employee.

    This REPLACES the existing schedule entirely.
    If you send Monday + Wednesday, any other days previously set are removed.

    Steps:
    1. Verify ownership chain
    2. Delete all existing working hours for this employee
    3. Insert the new ones

    Args:
        db:          database session
        employee_id: id of the employee
        agent_id:    id of the agent
        data:        validated WorkingHoursSet schema
        business_id: id of the logged in business

    Returns:
        dict with new working hours list and total
    """

    # verify ownership
    get_employee(db, employee_id, agent_id, business_id)

    # Step 2 — delete all existing working hours for this employee
    db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee_id
    ).delete()

    # Step 3 — insert the new schedule
    new_hours = []
    for wh in data.working_hours:
        working_hour = EmployeeWorkingHour(
            employee_id=employee_id,
            day_of_week=wh.day_of_week,
            start_time=wh.start_time,
            end_time=wh.end_time,
        )
        db.add(working_hour)
        new_hours.append(working_hour)

    db.commit()

    # refresh all to get their auto-generated ids
    for wh in new_hours:
        db.refresh(wh)

    logger.info(f"Working hours set for employee_id={employee_id}: {len(new_hours)} days")

    return {
        "working_hours": new_hours,
        "total": len(new_hours),
    }


def get_working_hours(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get the weekly working schedule for an employee.

    Returns:
        dict with working hours list and total
    """

    get_employee(db, employee_id, agent_id, business_id)

    hours = db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee_id
    ).order_by(EmployeeWorkingHour.day_of_week).all()

    return {
        "working_hours": hours,
        "total": len(hours),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED TIMES
# ─────────────────────────────────────────────────────────────────────────────

def add_blocked_time(
    db: Session,
    employee_id: int,
    agent_id: int,
    data: BlockedTimeCreate,
    business_id: int,
) -> BlockedTime:
    """
    Add a recurring weekly blocked time for an employee.

    Used for regular unavailability that repeats every week —
    lunch breaks, team meetings, personal recurring commitments.

    Args:
        db:          database session
        employee_id: id of the employee
        agent_id:    id of the agent
        data:        validated BlockedTimeCreate schema
        business_id: id of the logged in business

    Returns:
        BlockedTime SQLAlchemy object
    """

    get_employee(db, employee_id, agent_id, business_id)

    blocked = BlockedTime(
        employee_id=employee_id,
        day_of_week=data.day_of_week,
        start_time=data.start_time,
        end_time=data.end_time,
        reason=data.reason,
    )

    db.add(blocked)
    db.commit()
    db.refresh(blocked)

    logger.info(
        f"Blocked time added for employee_id={employee_id} "
        f"on day_of_week={data.day_of_week} "
        f"{data.start_time}–{data.end_time}"
    )

    return blocked


def get_blocked_times(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get all blocked times for an employee.

    Returns:
        dict with blocked times list and total
    """

    get_employee(db, employee_id, agent_id, business_id)

    blocked_times = db.query(BlockedTime).filter(
        BlockedTime.employee_id == employee_id
    ).order_by(BlockedTime.day_of_week).all()

    return {
        "blocked_times": blocked_times,
        "total": len(blocked_times),
    }


def delete_blocked_time(
    db: Session,
    employee_id: int,
    agent_id: int,
    blocked_time_id: int,
    business_id: int,
) -> dict:
    """
    Remove a specific blocked time.

    Args:
        db:              database session
        employee_id:     id of the employee
        agent_id:        id of the agent
        blocked_time_id: id of the blocked time to remove
        business_id:     id of the logged in business

    Returns:
        dict with success message
    """

    get_employee(db, employee_id, agent_id, business_id)

    blocked = db.query(BlockedTime).filter(
        BlockedTime.id == blocked_time_id,
        BlockedTime.employee_id == employee_id,
    ).first()

    if not blocked:
        raise NotFoundException(detail="Blocked time not found")

    db.delete(blocked)
    db.commit()

    logger.info(f"Blocked time deleted: id={blocked_time_id} for employee_id={employee_id}")

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
    """
    Add a personal day off for an employee.

    Unlike agent-level days off (public holidays, branch closures),
    these are specific to one employee.

    Args:
        db:          database session
        employee_id: id of the employee
        agent_id:    id of the agent
        data:        validated DayOffCreate schema
        business_id: id of the logged in business

    Returns:
        DayOff SQLAlchemy object
    """

    get_employee(db, employee_id, agent_id, business_id)

    day_off = DayOff(
        agent_id=agent_id,
        employee_id=employee_id,     # set employee_id → personal day off
        name=data.name,
        start_date=data.start_date,
        end_date=data.end_date,
    )

    db.add(day_off)
    db.commit()
    db.refresh(day_off)

    logger.info(f"Day off added for employee_id={employee_id}: {data.start_date} to {data.end_date}")

    return day_off


def get_days_off(
    db: Session,
    employee_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get all personal days off for an employee.

    Returns:
        dict with days off list and total
    """

    get_employee(db, employee_id, agent_id, business_id)

    days_off = db.query(DayOff).filter(
        DayOff.employee_id == employee_id
    ).order_by(DayOff.start_date).all()

    return {
        "days_off": days_off,
        "total": len(days_off),
    }


def delete_day_off(
    db: Session,
    employee_id: int,
    agent_id: int,
    day_off_id: int,
    business_id: int,
) -> dict:
    """
    Remove a specific personal day off.

    Args:
        db:          database session
        employee_id: id of the employee
        agent_id:    id of the agent
        day_off_id:  id of the day off to remove
        business_id: id of the logged in business

    Returns:
        dict with success message
    """

    get_employee(db, employee_id, agent_id, business_id)

    day_off = db.query(DayOff).filter(
        DayOff.id == day_off_id,
        DayOff.employee_id == employee_id,
    ).first()

    if not day_off:
        raise NotFoundException(detail="Day off not found")

    db.delete(day_off)
    db.commit()

    logger.info(f"Day off deleted: id={day_off_id} for employee_id={employee_id}")

    return {"message": "Day off removed successfully"}

# ─────────────────────────────────────────────────────────────────────────────
# Employee Capabilities
# ─────────────────────────────────────────────────────────────────────────────

from sqlalchemy import or_
from app.models.models import EmployeeCapability, Service, ServiceTitle, ServiceVariation
from app.core.exceptions import ConflictException


def get_capabilities(db: Session, employee_id: int, agent_id: int, business_id: int) -> list:
    get_employee(db, employee_id, agent_id, business_id)
    return db.query(EmployeeCapability).filter(
        EmployeeCapability.employee_id == employee_id
    ).all()


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
    get_employee(db, employee_id, agent_id, business_id)

    # Validate exactly one target is provided
    provided = [x for x in (title_id, service_id, variation_id) if x is not None]
    if len(provided) != 1:
        raise ConflictException(
            detail="Provide exactly one of: title_id, service_id, variation_id",
            code="INVALID_CAPABILITY"
        )

    # Verify the target belongs to this agent
    if title_id:
        obj = db.query(ServiceTitle).filter(ServiceTitle.id == title_id, ServiceTitle.agent_id == agent_id).first()
        if not obj:
            raise NotFoundException(detail="Service title not found for this agent")
    elif service_id:
        obj = db.query(Service).filter(Service.id == service_id, Service.agent_id == agent_id).first()
        if not obj:
            raise NotFoundException(detail="Service not found for this agent")
    else:
        obj = db.query(ServiceVariation).join(Service).filter(
            ServiceVariation.id == variation_id, Service.agent_id == agent_id
        ).first()
        if not obj:
            raise NotFoundException(detail="Service variation not found for this agent")

    # Check for duplicate
    dup_filter = []
    if title_id:     dup_filter.append(EmployeeCapability.title_id == title_id)
    if service_id:   dup_filter.append(EmployeeCapability.service_id == service_id)
    if variation_id: dup_filter.append(EmployeeCapability.variation_id == variation_id)

    existing = db.query(EmployeeCapability).filter(
        EmployeeCapability.employee_id == employee_id,
        or_(*dup_filter),
    ).first()
    if existing:
        raise ConflictException(detail="Capability already assigned", code="ALREADY_ASSIGNED")

    from decimal import Decimal
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

    cap = db.query(EmployeeCapability).filter(
        EmployeeCapability.id == capability_id,
        EmployeeCapability.employee_id == employee_id,
    ).first()
    if not cap:
        raise NotFoundException(detail="Capability not found")

    db.delete(cap)
    db.commit()
    return {"message": "Capability removed successfully"}
