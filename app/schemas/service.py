"""
app/schemas/service.py

Pydantic schemas for service management.

Hierarchy:
  ServiceTitle  — grouping label (e.g. "Haircut")
    └─ Service  — bookable service (e.g. "Buzz Cut")
         └─ ServiceVariation  — optional per-price/duration option (e.g. "Fade")

A service with no variations carries price + duration_minutes directly.
A service with variations has price/duration_minutes as null; each variation
carries its own price and duration_minutes.
"""

from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
from decimal import Decimal


# ─────────────────────────────────────────────────────────────────────────────
# ServiceTitle schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceTitleCreate(BaseModel):
    agent_id: int
    name: str
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Title name cannot be empty")
        return v.strip()


class ServiceTitleUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Title name cannot be empty")
        return v.strip() if v else v


class ServiceTitleResponse(BaseModel):
    id: int
    agent_id: int
    name: str
    active: bool
    sort_order: int

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# ServiceVariation schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceVariationCreate(BaseModel):
    name: str
    price: Decimal
    duration_minutes: int
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Variation name cannot be empty")
        return v.strip()

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Price cannot be negative")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return v


class ServiceVariationUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[Decimal] = None
    duration_minutes: Optional[int] = None
    sort_order: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Variation name cannot be empty")
        return v.strip() if v else v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return v


class ServiceVariationResponse(BaseModel):
    id: int
    service_id: int
    name: str
    price: Decimal
    duration_minutes: int
    active: bool
    sort_order: int

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Service schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceCreate(BaseModel):
    agent_id: int
    title_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    # Provide price + duration_minutes for a flat-rate service (no variations).
    # Omit both when the service will use variations instead.
    duration_minutes: Optional[int] = None
    price: Optional[Decimal] = None
    sort_order: int = 0
    # Variations can be created inline with the service
    variations: list[ServiceVariationCreate] = []

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Service name cannot be empty")
        return v.strip()

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v

    @model_validator(mode="after")
    def price_or_variations_required(self):
        has_flat = self.price is not None and self.duration_minutes is not None
        has_variations = bool(self.variations)
        if not has_flat and not has_variations:
            raise ValueError(
                "Either provide price + duration_minutes for a flat-rate service, "
                "or provide at least one variation."
            )
        if has_flat and has_variations:
            raise ValueError(
                "A service cannot have both a flat price and variations. "
                "Remove price/duration_minutes or remove variations."
            )
        return self


class ServiceUpdate(BaseModel):
    title_id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[Decimal] = None
    sort_order: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Service name cannot be empty")
        return v.strip() if v else v

    @field_validator("duration_minutes")
    @classmethod
    def duration_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("Duration must be greater than 0 minutes")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v


class AssignEmployeeRequest(BaseModel):
    employee_id: int
    extra_price: Decimal = Decimal("0")

    @field_validator("extra_price")
    @classmethod
    def extra_price_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Extra price cannot be negative")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class ServiceResponse(BaseModel):
    id: int
    agent_id: int
    title_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[Decimal] = None
    active: bool
    sort_order: int
    variations: list[ServiceVariationResponse] = []

    model_config = {"from_attributes": True}


class ServiceListResponse(BaseModel):
    services: list[ServiceResponse]
    total: int


class ServiceTitleListResponse(BaseModel):
    titles: list[ServiceTitleResponse]
    total: int


class ServiceVariationListResponse(BaseModel):
    variations: list[ServiceVariationResponse]
    total: int


class ServiceTitleWithServicesResponse(BaseModel):
    """Title with its nested services (and their variations) — for the full catalogue view."""
    id: int
    agent_id: int
    name: str
    active: bool
    sort_order: int
    services: list[ServiceResponse] = []

    model_config = {"from_attributes": True}


class ServiceCatalogueResponse(BaseModel):
    """Full catalogue: titled groups + uncategorised services."""
    titles: list[ServiceTitleWithServicesResponse]
    uncategorised: list[ServiceResponse]


class ServiceEmployeeResponse(BaseModel):
    id: int
    name: str
    role: Optional[str] = None
    active: bool

    model_config = {"from_attributes": True}


class ServiceEmployeesResponse(BaseModel):
    employees: list[ServiceEmployeeResponse]
    total: int


class MessageResponse(BaseModel):
    message: str
