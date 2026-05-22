"""
app/schemas/service.py

Pydantic schemas for service management.

A service is something a business offers — e.g. "Haircut", "Massage", "Consultation".
Each service belongs to an agent and has:
  - a name and description
  - a duration in minutes
  - a price
  - an active/inactive status
  - a list of employees who can perform it (many-to-many)
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from decimal import Decimal


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceCreate(BaseModel):
    """
    Data required to create a new service.
    Used by: POST /api/v1/services/
    """
    agent_id: int
    name: str
    description: Optional[str] = None
    duration_minutes: int
    price: Decimal

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Service name cannot be empty")
        return value.strip()

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return value

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Price cannot be negative")
        return value


class ServiceUpdate(BaseModel):
    """
    Data for updating an existing service.
    All fields optional — only send what you want to change.
    Used by: PUT /api/v1/services/{id}
    """
    name: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[Decimal] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Service name cannot be empty")
        return value.strip() if value else value

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return value

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError("Price cannot be negative")
        return value


class AssignEmployeeRequest(BaseModel):
    """
    Data required to assign an employee to a service.
    Used by: POST /api/v1/services/{id}/assign-employee
    """
    employee_id: int


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceResponse(BaseModel):
    """Full service data returned to the client."""
    id: int
    agent_id: int
    name: str
    description: Optional[str] = None
    duration_minutes: int
    price: Decimal
    active: bool

    model_config = {"from_attributes": True}


class ServiceListResponse(BaseModel):
    """Wrapper for returning a list of services."""
    services: list[ServiceResponse]
    total: int


class ServiceEmployeeResponse(BaseModel):
    """
    Employee data returned when listing employees for a service.
    Simplified — just what the frontend needs to show who can do this service.
    """
    id: int
    name: str
    role: Optional[str] = None
    active: bool

    model_config = {"from_attributes": True}


class ServiceEmployeesResponse(BaseModel):
    """Wrapper for returning a list of employees assigned to a service."""
    employees: list[ServiceEmployeeResponse]
    total: int


class MessageResponse(BaseModel):
    """Simple message response for actions that don't return data."""
    message: str