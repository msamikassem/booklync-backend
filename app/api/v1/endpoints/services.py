"""
app/api/v1/endpoints/services.py

Service management routes — full hierarchy:
    ServiceTitle → Service → ServiceVariation

All endpoints are protected (JWT required).
Ownership chain verified on every operation: business → agent → resource.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
from app.schemas.service import (
    # Title
    ServiceTitleCreate,
    ServiceTitleUpdate,
    ServiceTitleResponse,
    # Service
    ServiceCreate,
    ServiceUpdate,
    ServiceResponse,
    ServiceListResponse,
    # Variation
    ServiceVariationCreate,
    ServiceVariationUpdate,
    ServiceVariationResponse,
    # Catalogue
    ServiceCatalogueResponse,
    # List responses
    ServiceTitleListResponse,
    ServiceVariationListResponse,
    # Employee assignment
    AssignEmployeeRequest,
    ServiceEmployeesResponse,
    # Generic
    MessageResponse,
)
from app.services import service_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# CATALOGUE  (must be before /{service_id} routes)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/catalogue",
    response_model=ServiceCatalogueResponse,
    status_code=200,
    summary="Get the full service catalogue for an agent",
)
def get_catalogue(
    agent_id: int = Query(..., description="Which agent to load the catalogue for"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Returns all service titles with their nested services and variations,
    plus any services that are not under a title (uncategorised).
    """
    current.require_branch(agent_id)
    return service_service.get_catalogue(db, agent_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE TITLE CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/titles/",
    response_model=ServiceTitleResponse,
    status_code=201,
    summary="Create a service title",
)
def create_title(
    data: ServiceTitleCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Create a new title group (e.g. 'Haircut') under an agent."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=data.agent_id)
    return service_service.create_title(db, data, current.business_id)


@router.get(
    "/titles/",
    response_model=ServiceTitleListResponse,
    status_code=200,
    summary="List all titles for an agent",
)
def get_titles(
    agent_id: int = Query(..., description="Which agent to list titles for"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Returns all service titles for a specific agent, ordered by sort_order."""
    current.require_branch(agent_id)
    return service_service.get_titles(db, agent_id, current.business_id)


@router.get(
    "/titles/{title_id}",
    response_model=ServiceTitleResponse,
    status_code=200,
    summary="Get a service title",
)
def get_title(
    title_id: int,
    agent_id: int = Query(..., description="The agent this title belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Get a single title by id."""
    current.require_branch(agent_id)
    return service_service.get_title(db, title_id, agent_id, current.business_id)


@router.put(
    "/titles/{title_id}",
    response_model=ServiceTitleResponse,
    status_code=200,
    summary="Update a service title",
)
def update_title(
    title_id: int,
    data: ServiceTitleUpdate,
    agent_id: int = Query(..., description="The agent this title belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Update name or sort_order of a title. Only send fields you want to change."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.update_title(db, title_id, agent_id, data, current.business_id)


@router.delete(
    "/titles/{title_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a service title",
)
def delete_title(
    title_id: int,
    agent_id: int = Query(..., description="The agent this title belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Permanently delete a title.
    All services (and their variations) under this title are also deleted.
    """
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.delete_title(db, title_id, agent_id, current.business_id)


@router.patch(
    "/titles/{title_id}/activate",
    response_model=ServiceTitleResponse,
    status_code=200,
    summary="Activate a service title",
)
def activate_title(
    title_id: int,
    agent_id: int = Query(..., description="The agent this title belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.activate_title(db, title_id, agent_id, current.business_id)


@router.patch(
    "/titles/{title_id}/deactivate",
    response_model=ServiceTitleResponse,
    status_code=200,
    summary="Deactivate a service title",
)
def deactivate_title(
    title_id: int,
    agent_id: int = Query(..., description="The agent this title belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.deactivate_title(db, title_id, agent_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=201,
    summary="Create a service",
)
def create_service(
    data: ServiceCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Create a new service.

    Flat-rate service (no variations):
        { "name": "Buzz Cut", "price": 25.00, "duration_minutes": 30, ... }

    Variation-based service (no flat price):
        { "name": "Buzz Cut", "variations": [{ "name": "Fade", "price": 30, "duration_minutes": 45 }], ... }

    Providing both flat price/duration AND variations in the same request is not allowed.
    Variations can also be added later via POST /{service_id}/variations/.
    """
    current.require(rbac.P_MANAGE_SERVICES, agent_id=data.agent_id)
    return service_service.create_service(db, data, current.business_id)


@router.get(
    "/",
    response_model=ServiceListResponse,
    status_code=200,
    summary="List all services for an agent",
)
def get_services(
    agent_id: int = Query(..., description="Which agent to list services for"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Returns all services (with their variations) for a specific agent."""
    current.require_branch(agent_id)
    return service_service.get_services(db, agent_id, current.business_id)


@router.get(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=200,
    summary="Get a service",
)
def get_service(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Get one service by id, including its variations."""
    current.require_branch(agent_id)
    return service_service.get_service(db, service_id, agent_id, current.business_id)


@router.put(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=200,
    summary="Update a service",
)
def update_service(
    service_id: int,
    data: ServiceUpdate,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Update a service. All fields are optional — only send what you want to change.
    To move a service into a title, send title_id. To remove it from a title, send title_id=null.
    """
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.update_service(db, service_id, agent_id, data, current.business_id)


@router.delete(
    "/{service_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a service",
)
def delete_service(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Permanently delete a service and all its variations, assignments, and appointments."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.delete_service(db, service_id, agent_id, current.business_id)


@router.patch(
    "/{service_id}/activate",
    response_model=ServiceResponse,
    status_code=200,
    summary="Activate a service",
)
def activate_service(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.activate_service(db, service_id, agent_id, current.business_id)


@router.patch(
    "/{service_id}/deactivate",
    response_model=ServiceResponse,
    status_code=200,
    summary="Deactivate a service",
)
def deactivate_service(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.deactivate_service(db, service_id, agent_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE VARIATION CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{service_id}/variations/",
    response_model=ServiceVariationResponse,
    status_code=201,
    summary="Add a variation to a service",
)
def add_variation(
    service_id: int,
    data: ServiceVariationCreate,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Add a variation (e.g. 'Fade') to an existing service.
    If the service was flat-rate, its price and duration_minutes are cleared
    and it becomes variation-based.
    """
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.add_variation(
        db, service_id, agent_id, data, current.business_id
    )


@router.get(
    "/{service_id}/variations/",
    response_model=ServiceVariationListResponse,
    status_code=200,
    summary="List all variations of a service",
)
def get_variations(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Returns all variations for a service, ordered by sort_order."""
    current.require_branch(agent_id)
    return service_service.get_variations(db, service_id, agent_id, current.business_id)


@router.put(
    "/{service_id}/variations/{variation_id}",
    response_model=ServiceVariationResponse,
    status_code=200,
    summary="Update a variation",
)
def update_variation(
    service_id: int,
    variation_id: int,
    data: ServiceVariationUpdate,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Update a variation's name, price, duration, or sort_order."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.update_variation(
        db, variation_id, service_id, agent_id, data, current.business_id
    )


@router.delete(
    "/{service_id}/variations/{variation_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a variation",
)
def delete_variation(
    service_id: int,
    variation_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Permanently delete a variation."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.delete_variation(
        db, variation_id, service_id, agent_id, current.business_id
    )


@router.patch(
    "/{service_id}/variations/{variation_id}/activate",
    response_model=ServiceVariationResponse,
    status_code=200,
    summary="Activate a variation",
)
def activate_variation(
    service_id: int,
    variation_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.activate_variation(
        db, variation_id, service_id, agent_id, current.business_id
    )


@router.patch(
    "/{service_id}/variations/{variation_id}/deactivate",
    response_model=ServiceVariationResponse,
    status_code=200,
    summary="Deactivate a variation",
)
def deactivate_variation(
    service_id: int,
    variation_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.deactivate_variation(
        db, variation_id, service_id, agent_id, current.business_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE ASSIGNMENTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{service_id}/assign-employee",
    response_model=MessageResponse,
    status_code=200,
    summary="Assign an employee to a service",
)
def assign_employee(
    service_id: int,
    data: AssignEmployeeRequest,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Grant a service-level capability to an employee."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.assign_employee(
        db, service_id, agent_id, data.employee_id, current.business_id,
        extra_price=data.extra_price,
    )


@router.delete(
    "/{service_id}/unassign-employee/{employee_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Unassign an employee from a service",
)
def unassign_employee(
    service_id: int,
    employee_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Remove a service-level capability from an employee."""
    current.require(rbac.P_MANAGE_SERVICES, agent_id=agent_id)
    return service_service.unassign_employee(
        db, service_id, agent_id, employee_id, current.business_id
    )


@router.get(
    "/{service_id}/employees",
    response_model=ServiceEmployeesResponse,
    status_code=200,
    summary="Get all employees who can perform a service",
)
def get_service_employees(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Returns all employees capable of performing this service.
    Includes employees granted at the variation, service, or title level.
    """
    current.require_branch(agent_id)
    return service_service.get_service_employees(
        db, service_id, agent_id, current.business_id
    )
