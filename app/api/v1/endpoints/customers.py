"""
app/api/v1/endpoints/customers.py

Customer management routes.
All endpoints are protected — require a valid JWT token.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
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
    current_business: Business = Depends(get_current_business),
):
    """Create a new customer for the logged in business."""
    return customer_service.create_customer(db, data, current_business.id)


@router.get(
    "/",
    response_model=CustomerListResponse,
    status_code=200,
    summary="Get all customers",
)
def get_customers(
    search: Optional[str] = Query(None, description="Search by name or phone"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all customers for the logged in business.
    Optional search by name or phone number.
    """
    return customer_service.get_customers(db, current_business.id, search)


@router.get(
    "/by-phone",
    response_model=CustomerResponse,
    status_code=200,
    summary="Find a customer by phone number",
)
def get_customer_by_phone(
    phone: str = Query(..., description="Phone number to search for"),
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Find a customer by their phone number.
    Used by the AI agent to look up callers.
    """
    return customer_service.get_customer_by_phone(db, phone, current_business.id)


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=200,
    summary="Get one customer",
)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Returns one customer by id."""
    return customer_service.get_customer(db, customer_id, current_business.id)


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
    current_business: Business = Depends(get_current_business),
):
    """Update an existing customer. All fields optional."""
    return customer_service.update_customer(db, customer_id, data, current_business.id)


@router.delete(
    "/{customer_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete a customer",
)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """Delete a customer and all their appointments."""
    return customer_service.delete_customer(db, customer_id, current_business.id)


@router.get(
    "/{customer_id}/appointments",
    status_code=200,
    summary="Get all appointments for a customer",
)
def get_customer_appointments(
    customer_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns full appointment history for a customer.
    Used by the AI agent to provide context during calls.
    """
    from app.services import appointment_service
    return appointment_service.get_customer_appointments(
        db, customer_id, current_business.id
    )