"""
app/services/service_service.py

All business logic for service management.

Ownership chain enforced on every operation:
    business → agent → service

The many-to-many between services and employees is managed
through the EmployeeService junction table.
"""

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import (
    Service,
    AIAgent,
    Employee,
    EmployeeService,
)
from app.schemas.service import (
    ServiceCreate,
    ServiceUpdate,
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


def _get_service_for_agent(db: Session, service_id: int, agent_id: int) -> Service:
    """
    Fetch a service and verify it belongs to the given agent.

    Raises:
        NotFoundException:  service not found
        ForbiddenException: service belongs to different agent
    """
    service = db.query(Service).filter(Service.id == service_id).first()

    if not service:
        raise NotFoundException(detail="Service not found")

    if service.agent_id != agent_id:
        raise ForbiddenException(detail="This service does not belong to this agent")

    return service


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_service(db: Session, data: ServiceCreate, business_id: int) -> Service:
    """
    Create a new service under an agent.

    Steps:
    1. Verify the agent belongs to the logged in business
    2. Check no duplicate service name for this agent
    3. Create the service record

    Args:
        db:          database session
        data:        validated ServiceCreate schema
        business_id: id of the logged in business

    Returns:
        Service SQLAlchemy object

    Raises:
        NotFoundException:  agent not found
        ForbiddenException: agent belongs to different business
        ConflictException:  service name already exists for this agent
    """

    # Step 1 — verify agent ownership
    _get_agent_for_business(db, data.agent_id, business_id)

    # Step 2 — check no duplicate name for this agent
    # the same name can exist under different agents
    # but not twice under the same agent
    existing = db.query(Service).filter(
        Service.agent_id == data.agent_id,
        Service.name == data.name,
    ).first()

    if existing:
        raise ConflictException(
            detail=f"A service named '{data.name}' already exists for this agent",
            code="SERVICE_NAME_TAKEN"
        )

    # Step 3 — create the service
    service = Service(
        agent_id=data.agent_id,
        name=data.name,
        description=data.description,
        duration_minutes=data.duration_minutes,
        price=data.price,
        active=True,    # services start active by default
    )

    db.add(service)
    db.commit()
    db.refresh(service)

    logger.info(f"Service created: '{service.name}' (id={service.id}) for agent_id={data.agent_id}")

    return service


def get_services(db: Session, agent_id: int, business_id: int) -> dict:
    """
    Get all services for a specific agent.

    Args:
        db:          database session
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with services list and total count
    """

    _get_agent_for_business(db, agent_id, business_id)

    services = db.query(Service).filter(
        Service.agent_id == agent_id
    ).all()

    return {
        "services": services,
        "total": len(services),
    }


def get_service(db: Session, service_id: int, agent_id: int, business_id: int) -> Service:
    """
    Get one specific service by id.

    Verifies the full ownership chain:
    business → agent → service

    Args:
        db:          database session
        service_id:  id of the service
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        Service SQLAlchemy object
    """

    _get_agent_for_business(db, agent_id, business_id)
    service = _get_service_for_agent(db, service_id, agent_id)

    return service


def update_service(
    db: Session,
    service_id: int,
    agent_id: int,
    data: ServiceUpdate,
    business_id: int,
) -> Service:
    """
    Update an existing service.
    Only updates fields that are actually sent.

    Args:
        db:          database session
        service_id:  id of the service to update
        agent_id:    id of the agent
        data:        validated ServiceUpdate schema
        business_id: id of the logged in business

    Returns:
        Updated Service SQLAlchemy object

    Raises:
        ConflictException: if new name already taken by another service
    """

    service = get_service(db, service_id, agent_id, business_id)

    # if name is being changed check it's not taken by another service under this agent
    if data.name and data.name != service.name:
        existing = db.query(Service).filter(
            Service.agent_id == agent_id,
            Service.name == data.name,
        ).first()
        if existing:
            raise ConflictException(
                detail=f"A service named '{data.name}' already exists for this agent",
                code="SERVICE_NAME_TAKEN"
            )

    # update only fields that were sent
    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(service, field, value)

    db.commit()
    db.refresh(service)

    logger.info(f"Service updated: id={service_id} by business_id={business_id}")

    return service


def delete_service(
    db: Session,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Delete a service and all its related data.

    Cascade deletes automatically remove:
    - employee service assignments (EmployeeService junction rows)
    - appointments linked to this service

    Args:
        db:          database session
        service_id:  id of the service to delete
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with success message
    """

    service = get_service(db, service_id, agent_id, business_id)
    name = service.name

    db.delete(service)
    db.commit()

    logger.info(f"Service deleted: id={service_id} by business_id={business_id}")

    return {"message": f"Service '{name}' deleted successfully"}


def activate_service(
    db: Session,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> Service:
    """Activate a service so it can be booked."""

    service = get_service(db, service_id, agent_id, business_id)
    service.active = True
    db.commit()
    db.refresh(service)

    logger.info(f"Service activated: id={service_id}")

    return service


def deactivate_service(
    db: Session,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> Service:
    """Deactivate a service so it cannot be booked."""

    service = get_service(db, service_id, agent_id, business_id)
    service.active = False
    db.commit()
    db.refresh(service)

    logger.info(f"Service deactivated: id={service_id}")

    return service


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE ASSIGNMENTS (many-to-many)
# ─────────────────────────────────────────────────────────────────────────────

def assign_employee(
    db: Session,
    service_id: int,
    agent_id: int,
    employee_id: int,
    business_id: int,
) -> dict:
    """
    Assign an employee to a service — meaning they can perform it.

    Steps:
    1. Verify service belongs to the agent
    2. Verify employee belongs to the same agent
    3. Check not already assigned
    4. Create the EmployeeService junction row

    Args:
        db:          database session
        service_id:  id of the service
        agent_id:    id of the agent
        employee_id: id of the employee to assign
        business_id: id of the logged in business

    Returns:
        dict with success message

    Raises:
        NotFoundException:  service or employee not found
        ForbiddenException: service/employee belongs to different agent
        ConflictException:  employee already assigned to this service
    """

    # verify service ownership
    get_service(db, service_id, agent_id, business_id)

    # verify employee belongs to the same agent
    employee = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.agent_id == agent_id,
    ).first()

    if not employee:
        raise NotFoundException(
            detail="Employee not found or does not belong to this agent"
        )

    # check not already assigned
    existing = db.query(EmployeeService).filter(
        EmployeeService.employee_id == employee_id,
        EmployeeService.service_id == service_id,
    ).first()

    if existing:
        raise ConflictException(
            detail=f"Employee '{employee.name}' is already assigned to this service",
            code="ALREADY_ASSIGNED"
        )

    # create the junction row
    assignment = EmployeeService(
        employee_id=employee_id,
        service_id=service_id,
    )

    db.add(assignment)
    db.commit()

    logger.info(f"Employee id={employee_id} assigned to service id={service_id}")

    return {"message": f"Employee '{employee.name}' assigned to service successfully"}


def unassign_employee(
    db: Session,
    service_id: int,
    agent_id: int,
    employee_id: int,
    business_id: int,
) -> dict:
    """
    Remove an employee from a service — they can no longer perform it.

    Args:
        db:          database session
        service_id:  id of the service
        agent_id:    id of the agent
        employee_id: id of the employee to unassign
        business_id: id of the logged in business

    Returns:
        dict with success message

    Raises:
        NotFoundException: assignment not found
    """

    # verify service ownership
    get_service(db, service_id, agent_id, business_id)

    # find the junction row
    assignment = db.query(EmployeeService).filter(
        EmployeeService.employee_id == employee_id,
        EmployeeService.service_id == service_id,
    ).first()

    if not assignment:
        raise NotFoundException(
            detail="This employee is not assigned to this service"
        )

    db.delete(assignment)
    db.commit()

    logger.info(f"Employee id={employee_id} unassigned from service id={service_id}")

    return {"message": "Employee unassigned from service successfully"}


def get_service_employees(
    db: Session,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get all employees who can perform a specific service.

    Joins through the EmployeeService junction table to get
    the actual Employee records.

    Args:
        db:          database session
        service_id:  id of the service
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with employees list and total count
    """

    get_service(db, service_id, agent_id, business_id)

    # join Employee through EmployeeService to get all assigned employees
    employees = db.query(Employee).join(
        EmployeeService,
        Employee.id == EmployeeService.employee_id,
    ).filter(
        EmployeeService.service_id == service_id,
    ).all()

    return {
        "employees": employees,
        "total": len(employees),
    }