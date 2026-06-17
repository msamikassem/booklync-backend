"""
app/schemas/agent.py

Pydantic schemas for AI agent management.

An AI agent represents one deployed receptionist:
  - has its own phone number (Twilio)
  - has its own timezone and location
  - belongs to one business
  - has employees, services, and business hours under it
"""

import re

import pytz
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
from decimal import Decimal


def _validate_currency(value: Optional[str]) -> Optional[str]:
    """Normalise + validate an ISO 4217 currency code (3 letters)."""
    if value is None:
        return value
    v = value.strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("Currency must be a 3-letter ISO 4217 code (e.g. USD)")
    return v


def _validate_tax_rate(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is not None and (value < 0 or value > 100):
        raise ValueError("Tax rate must be a percentage between 0 and 100")
    return value


def _validate_timezone(value: Optional[str]) -> Optional[str]:
    """Validate an IANA timezone name (e.g. 'America/New_York'). Rejects typos
    that would otherwise be silently treated as UTC and break the tz-duality."""
    if value is None:
        return value
    v = value.strip()
    if not v:
        raise ValueError("Timezone cannot be empty")
    if v not in pytz.all_timezones_set:
        raise ValueError(f"'{v}' is not a valid IANA timezone (e.g. 'America/New_York', 'UTC')")
    return v


_PHONE_RE = re.compile(r"^\+?[0-9\s\-().]+$")


def _validate_phone(value: Optional[str]) -> Optional[str]:
    """Validate a phone number is plausibly E.164-ish (digits + common separators,
    7–15 digits). Rejects free-text like 'notaphone' that would break Twilio routing."""
    if value is None:
        return value
    v = value.strip()
    if not v:
        raise ValueError("Phone number cannot be empty")
    digits = re.sub(r"\D", "", v)
    if not _PHONE_RE.match(v) or not (7 <= len(digits) <= 15):
        raise ValueError("Phone number must be a valid phone (7–15 digits, e.g. +15551234567)")
    return v


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST schemas — data coming IN
# ─────────────────────────────────────────────────────────────────────────────

class AgentCreate(BaseModel):
    """
    Data required to create a new AI agent.
    Used by: POST /api/v1/agents/
    """
    agent_name: str
    phone_number: str
    location_name: str
    timezone: str           # IANA timezone string e.g. "America/New_York"
    model: str              # AI model to use e.g. "claude-sonnet-4-20250514"
    sms_welcome_message: Optional[str] = None
    sms_post_welcome_behavior: Optional[str] = "ai"
    # Per-branch financials (see docs/modules/financials.md)
    currency: str = "USD"                       # ISO 4217
    tax_rate: Optional[Decimal] = None          # percent, e.g. 8.25 (null = no tax)
    tax_inclusive: bool = False

    _v_currency = field_validator("currency")(_validate_currency)
    _v_tax_rate = field_validator("tax_rate")(_validate_tax_rate)
    _v_phone = field_validator("phone_number")(_validate_phone)
    _v_timezone = field_validator("timezone")(_validate_timezone)

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Agent name cannot be empty")
        return value.strip()

    @field_validator("location_name")
    @classmethod
    def location_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Location name cannot be empty")
        return value.strip()
    
    @field_validator("sms_post_welcome_behavior")  # 👈
    @classmethod
    def validate_post_welcome_behavior(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("ai", "silent"):
            raise ValueError("sms_post_welcome_behavior must be 'ai' or 'silent'")
        return value


class AgentUpdate(BaseModel):
    """
    Data for updating an existing agent.
    All fields are optional — only send what you want to change.
    Used by: PUT /api/v1/agents/{id}
    """
    agent_name: Optional[str] = None
    phone_number: Optional[str] = None
    location_name: Optional[str] = None
    timezone: Optional[str] = None
    model: Optional[str] = None
    sms_welcome_message: Optional[str] = None
    sms_post_welcome_behavior: Optional[str] = None
    currency: Optional[str] = None
    tax_rate: Optional[Decimal] = None
    tax_inclusive: Optional[bool] = None

    _v_currency = field_validator("currency")(_validate_currency)
    _v_tax_rate = field_validator("tax_rate")(_validate_tax_rate)
    _v_phone = field_validator("phone_number")(_validate_phone)
    _v_timezone = field_validator("timezone")(_validate_timezone)

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Agent name cannot be empty")
        return value.strip() if value else value

    @field_validator("sms_post_welcome_behavior")  # 👈
    @classmethod
    def validate_post_welcome_behavior(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("ai", "silent"):
            raise ValueError("sms_post_welcome_behavior must be 'ai' or 'silent'")
        return value


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE schemas — data going OUT
# ─────────────────────────────────────────────────────────────────────────────

class AgentResponse(BaseModel):
    """
    Full agent data returned to the client.
    """
    id: int
    business_id: int
    agent_name: str
    phone_number: str
    location_name: str
    timezone: str
    model: str
    sms_welcome_message: Optional[str] = None
    sms_post_welcome_behavior: Optional[str] = None
    booking_page_slug: Optional[str] = None
    custom_domain: Optional[str] = None
    domain_verified: bool = False
    currency: str = "USD"
    tax_rate: Optional[Decimal] = None
    tax_inclusive: bool = False
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentListResponse(BaseModel):
    """
    Wrapper for returning a list of agents.
    Includes a count so the frontend knows how many total.
    """
    agents: list[AgentResponse]
    total: int


class MessageResponse(BaseModel):
    """Simple message response for actions that don't return data."""
    message: str