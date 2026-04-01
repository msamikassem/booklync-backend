"""
app/services/customer_service.py

All business logic for customer management.

Customers belong to a business.
Phone number is unique per business — the primary identifier
used by the AI agent to look up returning customers.
"""

from sqlalchemy.orm import Session
from loguru import logger
from typing import Optional
from app.models.models import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
)


def create_customer(
    db: Session,
    data: CustomerCreate,
    business_id: int,
) -> Customer:
    """
    Create a new customer for the logged in business.

    Phone number must be unique within the business.

    Args:
        db:          database session
        data:        validated CustomerCreate schema
        business_id: id of the logged in business

    Returns:
        Customer SQLAlchemy object

    Raises:
        ConflictException: phone number already registered for this business
    """

    existing = db.query(Customer).filter(
        Customer.business_id == business_id,
        Customer.phone == data.phone,
    ).first()

    if existing:
        raise ConflictException(
            detail=f"A customer with phone '{data.phone}' already exists",
            code="PHONE_TAKEN"
        )

    customer = Customer(
        business_id=business_id,
        name=data.name,
        phone=data.phone,
        email=data.email,
        notes=data.notes,
    )

    db.add(customer)
    db.commit()
    db.refresh(customer)

    logger.info(
        f"Customer created: '{customer.name}' phone={customer.phone} "
        f"(id={customer.id}) for business_id={business_id}"
    )

    return customer


def get_or_create_customer(
    db: Session,
    phone: str,
    name: str,
    business_id: int,
    email: Optional[str] = None,
) -> Customer:
    """
    Get an existing customer by phone or create a new one.

    Used by the AI agent — when a call/SMS comes in, look up the
    customer by phone number. If they're new, create them automatically.

    Args:
        db:          database session
        phone:       customer's phone number
        name:        customer's name (used only if creating)
        business_id: id of the business
        email:       optional email (used only if creating)

    Returns:
        Customer SQLAlchemy object (existing or newly created)
    """

    customer = db.query(Customer).filter(
        Customer.business_id == business_id,
        Customer.phone == phone,
    ).first()

    if customer:
        logger.info(f"Returning customer found: {customer.name} (id={customer.id})")
        return customer

    # new customer — create them
    customer = Customer(
        business_id=business_id,
        name=name,
        phone=phone,
        email=email,
    )

    db.add(customer)
    db.commit()
    db.refresh(customer)

    logger.info(
        f"New customer auto-created: '{customer.name}' phone={phone} "
        f"(id={customer.id}) for business_id={business_id}"
    )

    return customer


def get_customers(
    db: Session,
    business_id: int,
    search: str = None,
) -> dict:
    """
    Get all customers for the logged in business.

    Optional search by name or phone number.

    Args:
        db:          database session
        business_id: id of the logged in business
        search:      optional search string (name or phone)

    Returns:
        dict with customers list and total count
    """

    query = db.query(Customer).filter(
        Customer.business_id == business_id
    )

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            Customer.name.ilike(search_term) |
            Customer.phone.ilike(search_term)
        )

    customers = query.order_by(Customer.created_at.desc()).all()

    return {
        "customers": customers,
        "total": len(customers),
    }


def get_customer(
    db: Session,
    customer_id: int,
    business_id: int,
) -> Customer:
    """
    Get one specific customer by id.
    Verifies the customer belongs to the logged in business.

    Args:
        db:          database session
        customer_id: id of the customer
        business_id: id of the logged in business

    Returns:
        Customer SQLAlchemy object

    Raises:
        NotFoundException: customer not found or belongs to different business
    """

    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.business_id == business_id,
    ).first()

    if not customer:
        raise NotFoundException(detail="Customer not found")

    return customer


def get_customer_by_phone(
    db: Session,
    phone: str,
    business_id: int,
) -> Customer:
    """
    Find a customer by phone number.
    Used by the AI agent to look up callers.

    Args:
        db:          database session
        phone:       phone number to look up
        business_id: id of the business

    Returns:
        Customer SQLAlchemy object

    Raises:
        NotFoundException: no customer with this phone number
    """

    customer = db.query(Customer).filter(
        Customer.phone == phone,
        Customer.business_id == business_id,
    ).first()

    if not customer:
        raise NotFoundException(
            detail=f"No customer found with phone '{phone}'"
        )

    return customer


def update_customer(
    db: Session,
    customer_id: int,
    data: CustomerUpdate,
    business_id: int,
) -> Customer:
    """
    Update an existing customer.
    Only updates fields that are actually sent.

    Args:
        db:          database session
        customer_id: id of the customer
        data:        validated CustomerUpdate schema
        business_id: id of the logged in business

    Returns:
        Updated Customer SQLAlchemy object

    Raises:
        ConflictException: new phone already taken by another customer
    """

    customer = get_customer(db, customer_id, business_id)

    # if phone is being changed check it's not taken
    if data.phone and data.phone != customer.phone:
        existing = db.query(Customer).filter(
            Customer.business_id == business_id,
            Customer.phone == data.phone,
        ).first()
        if existing:
            raise ConflictException(
                detail=f"A customer with phone '{data.phone}' already exists",
                code="PHONE_TAKEN"
            )

    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(customer, field, value)

    db.commit()
    db.refresh(customer)

    logger.info(f"Customer updated: id={customer_id}")

    return customer


def delete_customer(
    db: Session,
    customer_id: int,
    business_id: int,
) -> dict:
    """
    Delete a customer and all their appointments.

    Args:
        db:          database session
        customer_id: id of the customer
        business_id: id of the logged in business

    Returns:
        dict with success message
    """

    customer = get_customer(db, customer_id, business_id)
    name = customer.name

    db.delete(customer)
    db.commit()

    logger.info(f"Customer deleted: id={customer_id} name='{name}'")

    return {"message": f"Customer '{name}' deleted successfully"}


# fix missing Optional import
from typing import Optional