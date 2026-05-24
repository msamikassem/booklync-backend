"""
app/services/service_service.py

Business logic for the full service hierarchy:
    ServiceTitle → Service → ServiceVariation

Ownership chain enforced on every operation:
    business → agent → (title|service|variation)
"""

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_
from loguru import logger

from app.models.models import (
    Service,
    ServiceVariation,
    ServiceTitle,
    AIAgent,
    Employee,
    EmployeeCapability,
)
from app.schemas.service import (
    ServiceCreate,
    ServiceUpdate,
    ServiceTitleCreate,
    ServiceTitleUpdate,
    ServiceVariationCreate,
    ServiceVariationUpdate,
)
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    ConflictException,
)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")
    return agent


def _get_service_for_agent(db: Session, service_id: int, agent_id: int) -> Service:
    """Bare service fetch without eager loading — for internal use."""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise NotFoundException(detail="Service not found")
    if service.agent_id != agent_id:
        raise ForbiddenException(detail="This service does not belong to this agent")
    return service


def _get_variation_for_service(
    db: Session, variation_id: int, service_id: int
) -> ServiceVariation:
    variation = db.query(ServiceVariation).filter(
        ServiceVariation.id == variation_id,
        ServiceVariation.service_id == service_id,
    ).first()
    if not variation:
        raise NotFoundException(
            detail="Variation not found or does not belong to this service"
        )
    return variation


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE TITLE CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_title(
    db: Session, data: ServiceTitleCreate, business_id: int
) -> ServiceTitle:
    _get_agent_for_business(db, data.agent_id, business_id)

    existing = db.query(ServiceTitle).filter(
        ServiceTitle.agent_id == data.agent_id,
        ServiceTitle.name == data.name,
    ).first()
    if existing:
        raise ConflictException(
            detail=f"A title named '{data.name}' already exists for this agent",
            code="TITLE_NAME_TAKEN",
        )

    title = ServiceTitle(
        agent_id=data.agent_id,
        name=data.name,
        sort_order=data.sort_order,
        active=True,
    )
    db.add(title)
    db.commit()
    db.refresh(title)
    logger.info(f"ServiceTitle created: '{title.name}' (id={title.id}) agent_id={data.agent_id}")
    return title


def get_titles(db: Session, agent_id: int, business_id: int) -> dict:
    _get_agent_for_business(db, agent_id, business_id)
    titles = (
        db.query(ServiceTitle)
        .filter(ServiceTitle.agent_id == agent_id)
        .order_by(ServiceTitle.sort_order, ServiceTitle.id)
        .all()
    )
    return {"titles": titles, "total": len(titles)}


def get_title(
    db: Session, title_id: int, agent_id: int, business_id: int
) -> ServiceTitle:
    _get_agent_for_business(db, agent_id, business_id)
    title = db.query(ServiceTitle).filter(
        ServiceTitle.id == title_id,
        ServiceTitle.agent_id == agent_id,
    ).first()
    if not title:
        raise NotFoundException(detail="Service title not found")
    return title


def update_title(
    db: Session,
    title_id: int,
    agent_id: int,
    data: ServiceTitleUpdate,
    business_id: int,
) -> ServiceTitle:
    title = get_title(db, title_id, agent_id, business_id)

    if data.name and data.name != title.name:
        existing = db.query(ServiceTitle).filter(
            ServiceTitle.agent_id == agent_id,
            ServiceTitle.name == data.name,
        ).first()
        if existing:
            raise ConflictException(
                detail=f"A title named '{data.name}' already exists for this agent",
                code="TITLE_NAME_TAKEN",
            )

    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(title, field, value)

    db.commit()
    db.refresh(title)
    logger.info(f"ServiceTitle updated: id={title_id}")
    return title


def delete_title(
    db: Session, title_id: int, agent_id: int, business_id: int
) -> dict:
    title = get_title(db, title_id, agent_id, business_id)
    name = title.name
    # cascade="all, delete" on ServiceTitle.services deletes child services + their variations
    db.delete(title)
    db.commit()
    logger.info(f"ServiceTitle deleted: id={title_id}")
    return {"message": f"Title '{name}' and all its services deleted successfully"}


def activate_title(
    db: Session, title_id: int, agent_id: int, business_id: int
) -> ServiceTitle:
    title = get_title(db, title_id, agent_id, business_id)
    title.active = True
    db.commit()
    db.refresh(title)
    return title


def deactivate_title(
    db: Session, title_id: int, agent_id: int, business_id: int
) -> ServiceTitle:
    title = get_title(db, title_id, agent_id, business_id)
    title.active = False
    db.commit()
    db.refresh(title)
    return title


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_service(
    db: Session, data: ServiceCreate, business_id: int
) -> Service:
    _get_agent_for_business(db, data.agent_id, business_id)

    # validate title belongs to the same agent
    if data.title_id:
        title = db.query(ServiceTitle).filter(
            ServiceTitle.id == data.title_id,
            ServiceTitle.agent_id == data.agent_id,
        ).first()
        if not title:
            raise NotFoundException(
                detail="Service title not found or does not belong to this agent"
            )

    existing = db.query(Service).filter(
        Service.agent_id == data.agent_id,
        Service.name == data.name,
    ).first()
    if existing:
        raise ConflictException(
            detail=f"A service named '{data.name}' already exists for this agent",
            code="SERVICE_NAME_TAKEN",
        )

    service = Service(
        agent_id=data.agent_id,
        title_id=data.title_id,
        name=data.name,
        description=data.description,
        duration_minutes=data.duration_minutes,
        price=data.price,
        sort_order=data.sort_order,
        active=True,
    )
    db.add(service)
    db.flush()  # get service.id before inserting variations

    for v in data.variations:
        db.add(ServiceVariation(
            service_id=service.id,
            name=v.name,
            price=v.price,
            duration_minutes=v.duration_minutes,
            sort_order=v.sort_order,
            active=True,
        ))

    db.commit()
    db.refresh(service)
    logger.info(f"Service created: '{service.name}' (id={service.id}) agent_id={data.agent_id}")
    return service


def get_services(db: Session, agent_id: int, business_id: int) -> dict:
    _get_agent_for_business(db, agent_id, business_id)
    services = (
        db.query(Service)
        .options(selectinload(Service.variations))
        .filter(Service.agent_id == agent_id)
        .order_by(Service.sort_order, Service.id)
        .all()
    )
    return {"services": services, "total": len(services)}


def get_service(
    db: Session, service_id: int, agent_id: int, business_id: int
) -> Service:
    _get_agent_for_business(db, agent_id, business_id)
    service = (
        db.query(Service)
        .options(selectinload(Service.variations))
        .filter(Service.id == service_id)
        .first()
    )
    if not service:
        raise NotFoundException(detail="Service not found")
    if service.agent_id != agent_id:
        raise ForbiddenException(detail="This service does not belong to this agent")
    return service


def update_service(
    db: Session,
    service_id: int,
    agent_id: int,
    data: ServiceUpdate,
    business_id: int,
) -> Service:
    service = get_service(db, service_id, agent_id, business_id)

    if data.name and data.name != service.name:
        existing = db.query(Service).filter(
            Service.agent_id == agent_id,
            Service.name == data.name,
        ).first()
        if existing:
            raise ConflictException(
                detail=f"A service named '{data.name}' already exists for this agent",
                code="SERVICE_NAME_TAKEN",
            )

    if data.title_id is not None:
        title = db.query(ServiceTitle).filter(
            ServiceTitle.id == data.title_id,
            ServiceTitle.agent_id == agent_id,
        ).first()
        if not title:
            raise NotFoundException(
                detail="Service title not found or does not belong to this agent"
            )

    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(service, field, value)

    db.commit()
    db.refresh(service)
    logger.info(f"Service updated: id={service_id}")
    return service


def delete_service(
    db: Session, service_id: int, agent_id: int, business_id: int
) -> dict:
    service = get_service(db, service_id, agent_id, business_id)
    name = service.name
    db.delete(service)
    db.commit()
    logger.info(f"Service deleted: id={service_id}")
    return {"message": f"Service '{name}' deleted successfully"}


def activate_service(
    db: Session, service_id: int, agent_id: int, business_id: int
) -> Service:
    service = get_service(db, service_id, agent_id, business_id)
    service.active = True
    db.commit()
    db.refresh(service)
    return service


def deactivate_service(
    db: Session, service_id: int, agent_id: int, business_id: int
) -> Service:
    service = get_service(db, service_id, agent_id, business_id)
    service.active = False
    db.commit()
    db.refresh(service)
    return service


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE VARIATION CRUD
# ─────────────────────────────────────────────────────────────────────────────

def add_variation(
    db: Session,
    service_id: int,
    agent_id: int,
    data: ServiceVariationCreate,
    business_id: int,
) -> ServiceVariation:
    service = get_service(db, service_id, agent_id, business_id)

    existing = db.query(ServiceVariation).filter(
        ServiceVariation.service_id == service_id,
        ServiceVariation.name == data.name,
    ).first()
    if existing:
        raise ConflictException(
            detail=f"A variation named '{data.name}' already exists for this service",
            code="VARIATION_NAME_TAKEN",
        )

    variation = ServiceVariation(
        service_id=service_id,
        name=data.name,
        price=data.price,
        duration_minutes=data.duration_minutes,
        sort_order=data.sort_order,
        active=True,
    )
    db.add(variation)

    # converting a flat-rate service to variation-based: clear the flat fields
    if service.price is not None or service.duration_minutes is not None:
        service.price = None
        service.duration_minutes = None

    db.commit()
    db.refresh(variation)
    logger.info(f"ServiceVariation created: '{variation.name}' (id={variation.id}) service_id={service_id}")
    return variation


def get_variations(
    db: Session, service_id: int, agent_id: int, business_id: int
) -> dict:
    get_service(db, service_id, agent_id, business_id)
    variations = (
        db.query(ServiceVariation)
        .filter(ServiceVariation.service_id == service_id)
        .order_by(ServiceVariation.sort_order, ServiceVariation.id)
        .all()
    )
    return {"variations": variations, "total": len(variations)}


def update_variation(
    db: Session,
    variation_id: int,
    service_id: int,
    agent_id: int,
    data: ServiceVariationUpdate,
    business_id: int,
) -> ServiceVariation:
    get_service(db, service_id, agent_id, business_id)
    variation = _get_variation_for_service(db, variation_id, service_id)

    if data.name and data.name != variation.name:
        existing = db.query(ServiceVariation).filter(
            ServiceVariation.service_id == service_id,
            ServiceVariation.name == data.name,
        ).first()
        if existing:
            raise ConflictException(
                detail=f"A variation named '{data.name}' already exists for this service",
                code="VARIATION_NAME_TAKEN",
            )

    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(variation, field, value)

    db.commit()
    db.refresh(variation)
    logger.info(f"ServiceVariation updated: id={variation_id}")
    return variation


def delete_variation(
    db: Session,
    variation_id: int,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    get_service(db, service_id, agent_id, business_id)
    variation = _get_variation_for_service(db, variation_id, service_id)
    name = variation.name
    db.delete(variation)
    db.commit()
    logger.info(f"ServiceVariation deleted: id={variation_id}")
    return {"message": f"Variation '{name}' deleted successfully"}


def activate_variation(
    db: Session,
    variation_id: int,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> ServiceVariation:
    get_service(db, service_id, agent_id, business_id)
    variation = _get_variation_for_service(db, variation_id, service_id)
    variation.active = True
    db.commit()
    db.refresh(variation)
    return variation


def deactivate_variation(
    db: Session,
    variation_id: int,
    service_id: int,
    agent_id: int,
    business_id: int,
) -> ServiceVariation:
    get_service(db, service_id, agent_id, business_id)
    variation = _get_variation_for_service(db, variation_id, service_id)
    variation.active = False
    db.commit()
    db.refresh(variation)
    return variation


# ─────────────────────────────────────────────────────────────────────────────
# CATALOGUE
# ─────────────────────────────────────────────────────────────────────────────

def get_catalogue(db: Session, agent_id: int, business_id: int) -> dict:
    """
    Full service catalogue for an agent.
    Returns titled groups (each with their services and variations)
    plus any uncategorised services.
    """
    _get_agent_for_business(db, agent_id, business_id)

    titles = (
        db.query(ServiceTitle)
        .options(
            selectinload(ServiceTitle.services).selectinload(Service.variations)
        )
        .filter(ServiceTitle.agent_id == agent_id)
        .order_by(ServiceTitle.sort_order, ServiceTitle.id)
        .all()
    )

    uncategorised = (
        db.query(Service)
        .options(selectinload(Service.variations))
        .filter(
            Service.agent_id == agent_id,
            Service.title_id == None,
        )
        .order_by(Service.sort_order, Service.id)
        .all()
    )

    return {"titles": titles, "uncategorised": uncategorised}


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE ASSIGNMENTS (many-to-many via EmployeeCapability)
# ─────────────────────────────────────────────────────────────────────────────

def assign_employee(
    db: Session,
    service_id: int,
    agent_id: int,
    employee_id: int,
    business_id: int,
    extra_price=None,
) -> dict:
    get_service(db, service_id, agent_id, business_id)

    employee = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.agent_id == agent_id,
    ).first()
    if not employee:
        raise NotFoundException(
            detail="Employee not found or does not belong to this agent"
        )

    # block if already covered at service or title level
    service_obj = _get_service_for_agent(db, service_id, agent_id)
    conditions = [EmployeeCapability.service_id == service_id]
    if service_obj.title_id:
        conditions.append(EmployeeCapability.title_id == service_obj.title_id)

    existing = db.query(EmployeeCapability).filter(
        EmployeeCapability.employee_id == employee_id,
        or_(*conditions),
    ).first()
    if existing:
        raise ConflictException(
            detail=f"Employee '{employee.name}' is already assigned to this service or its title",
            code="ALREADY_ASSIGNED",
        )

    from decimal import Decimal
    db.add(EmployeeCapability(
        employee_id=employee_id,
        service_id=service_id,
        extra_price=extra_price if extra_price is not None else Decimal("0"),
    ))
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
    get_service(db, service_id, agent_id, business_id)

    assignment = db.query(EmployeeCapability).filter(
        EmployeeCapability.employee_id == employee_id,
        EmployeeCapability.service_id == service_id,
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
    get_service(db, service_id, agent_id, business_id)

    service_obj = _get_service_for_agent(db, service_id, agent_id)
    variation_ids = db.query(ServiceVariation.id).filter(
        ServiceVariation.service_id == service_id
    ).scalar_subquery()

    conditions = [
        EmployeeCapability.service_id == service_id,
        EmployeeCapability.variation_id.in_(variation_ids),
    ]
    if service_obj.title_id:
        conditions.append(EmployeeCapability.title_id == service_obj.title_id)

    capable_emp_ids = (
        db.query(EmployeeCapability.employee_id)
        .filter(or_(*conditions))
        .subquery()
    )

    employees = db.query(Employee).filter(Employee.id.in_(capable_emp_ids)).all()
    return {"employees": employees, "total": len(employees)}
