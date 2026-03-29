"""
app/api/v1/endpoints/services.py

Service management routes.

All endpoints are protected — require a valid JWT token.
Uses agent_id as a query parameter to verify the ownership chain:
    business → agent → service
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.service import (
    ServiceCreate,
    ServiceUpdate,
    AssignEmployeeRequest,
    ServiceResponse,
    ServiceListResponse,
    ServiceEmployeesResponse,
    MessageResponse,
)
from app.services import service_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=201,
    summary="Create a new service",
)
def create_service(
    data: ServiceCreate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Create a new service under an agent.
    The agent_id is required in the request body.
    Services start active by default.
    """
    return service_service.create_service(db, data, current_business.id)


@router.get(
    "/",
    response_model=ServiceListResponse,
    status_code=200,
    summary="Get all services for an agent",
)
def get_services(
    agent_id: int = Query(..., description="The agent to get services for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all services for a specific agent.
    Pass agent_id as a query parameter: /api/v1/services/?agent_id=1
    """
    return service_service.get_services(db, agent_id, current_business.id)


@router.get(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=200,
    summary="Get one specific service",
)
def get_service(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns one service by id."""
    return service_service.get_service(db, service_id, agent_id, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """
    Update an existing service. All fields are optional.
    Only send the fields you want to change.
    """
    return service_service.update_service(db, service_id, agent_id, data, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """
    Permanently delete a service.
    Removes all employee assignments and linked appointments.
    """
    return service_service.delete_service(db, service_id, agent_id, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """Activate a service so it can be booked."""
    return service_service.activate_service(db, service_id, agent_id, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """Deactivate a service so it cannot be booked."""
    return service_service.deactivate_service(db, service_id, agent_id, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """
    Assign an employee to a service — meaning they can perform it.
    Both the service and employee must belong to the same agent.
    """
    return service_service.assign_employee(
        db, service_id, agent_id, data.employee_id, current_business.id
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
    current_business: Business = Depends(get_current_business),
):
    """Remove an employee from a service — they can no longer perform it."""
    return service_service.unassign_employee(
        db, service_id, agent_id, employee_id, current_business.id
    )


@router.get(
    "/{service_id}/employees",
    response_model=ServiceEmployeesResponse,
    status_code=200,
    summary="Get all employees assigned to a service",
)
def get_service_employees(
    service_id: int,
    agent_id: int = Query(..., description="The agent this service belongs to"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns all employees who can perform this service."""
    return service_service.get_service_employees(
        db, service_id, agent_id, current_business.id
    )