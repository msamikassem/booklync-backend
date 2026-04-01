"""
app/schemas/customer.py

Pydantic schemas for customer management.

Customers belong to a business (not an agent).
The same customer can book with different agents of the same business
without creating duplicate records.
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class CustomerCreate(BaseModel):
    """
    Data required to create a new customer.
    Used by: POST /api/v1/customers/
    phone is required — it is the primary identifier for AI agent lookups.
    """
    name: str
    phone: str
    email: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Customer name cannot be empty")
        return value.strip()

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Phone number cannot be empty")
        return value.strip()


class CustomerUpdate(BaseModel):
    """
    Data for updating an existing customer.
    All fields optional.
    Used by: PUT /api/v1/customers/{id}
    """
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Customer name cannot be empty")
        return value.strip() if value else value

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Phone number cannot be empty")
        return value.strip() if value else value


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas
# ─────────────────────────────────────────────────────────────────────────────

class CustomerResponse(BaseModel):
    id: int
    business_id: int
    name: str
    phone: str
    email: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerListResponse(BaseModel):
    customers: list[CustomerResponse]
    total: int


class MessageResponse(BaseModel):
    message: str