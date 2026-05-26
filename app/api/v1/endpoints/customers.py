"""
app/api/v1/endpoints/customers.py

Customer management routes.
All endpoints are protected — require a valid JWT token.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
from app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerListResponse,
    MessageResponse,
)
from app.services import customer_service

router = APIRouter()


@router.post(
    "/",
    response_model=CustomerResponse,
    status_code=201,
    summary="Create a new customer",
)
def create_customer(
    data: CustomerCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Create a new customer for the logged in business."""
    current.require(rbac.P_MANAGE_CUSTOMERS)
    return customer_service.create_customer(db, data, current.business_id)


@router.get(
    "/",
    response_model=CustomerListResponse,
    status_code=200,
    summary="Get all customers",
)
def get_customers(
    search: Optional[str] = Query(None, description="Search by name or phone"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Returns all customers for the logged in business.
    Optional search by name or phone number.
    """
    current.require(rbac.P_VIEW_CUSTOMERS)
    return customer_service.get_customers(db, current.business_id, search)


@router.get(
    "/by-phone",
    response_model=CustomerResponse,
    status_code=200,
    summary="Find a customer by phone number",
)
def get_customer_by_phone(
    phone: str = Query(..., description="Phone number to search for"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Find a customer by their phone number.
    Used by the AI agent to look up callers.
    """
    current.require(rbac.P_VIEW_CUSTOMERS)
    return customer_service.get_customer_by_phone(db, phone, current.business_id)


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=200,
    summary="Get one customer",
)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Returns one customer by id."""
    current.require(rbac.P_VIEW_CUSTOMERS)
    return customer_service.get_customer(db, customer_id, current.business_id)


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=200,
    summary="Update a customer",
)
def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Update an existing customer. All fields optional."""
    current.require(rbac.P_MANAGE_CUSTOMERS)
    return customer_service.update_customer(db, customer_id, data, current.business_id)


@router.delete(
    "/{customer_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a customer",
)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Delete a customer and all their appointments."""
    current.require(rbac.P_MANAGE_CUSTOMERS)
    return customer_service.delete_customer(db, customer_id, current.business_id)


@router.get(
    "/{customer_id}/appointments",
    status_code=200,
    summary="Get all appointments for a customer",
)
def get_customer_appointments(
    customer_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Returns full appointment history for a customer.
    Used by the AI agent to provide context during calls.
    """
    current.require(rbac.P_VIEW_CUSTOMERS)
    from app.services import appointment_service
    return appointment_service.get_customer_appointments(
        db, customer_id, current.business_id
    )