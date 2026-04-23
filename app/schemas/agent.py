"""
app/schemas/agent.py

Pydantic schemas for AI agent management.

An AI agent represents one deployed receptionist:
  - has its own phone number (Twilio)
  - has its own timezone and location
  - belongs to one business
  - has employees, services, and business hours under it
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


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

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Agent name cannot be empty")
        return value.strip()

    @field_validator("phone_number")
    @classmethod
    def phone_number_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Phone number cannot be empty")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def timezone_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Timezone cannot be empty")
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

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Agent name cannot be empty")
        return value.strip() if value else value

    @field_validator("phone_number")
    @classmethod
    def phone_number_not_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Phone number cannot be empty")
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