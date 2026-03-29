"""
app/api/v1/endpoints/agents.py

AI agent management routes.

All endpoints here are protected — require a valid JWT token.
A business can only manage its own agents.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentListResponse,
    MessageResponse,
)
from app.services import agent_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=AgentResponse,
    status_code=201,
    summary="Create a new AI agent",
)
def create_agent(
    data: AgentCreate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Create a new AI agent for the logged in business.
    The agent starts inactive and must be activated after configuration.
    """
    return agent_service.create_agent(db, data, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# GET ALL
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=AgentListResponse,
    status_code=200,
    summary="Get all agents for the logged in business",
)
def get_agents(
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns all agents belonging to the logged in business.
    A business can never see another business's agents.
    """
    return agent_service.get_agents(db, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# GET ONE
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    status_code=200,
    summary="Get one specific agent",
)
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns one agent by id.
    Returns 403 if the agent belongs to a different business.
    """
    return agent_service.get_agent(db, agent_id, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────

@router.put(
    "/{agent_id}",
    response_model=AgentResponse,
    status_code=200,
    summary="Update an agent",
)
def update_agent(
    agent_id: int,
    data: AgentUpdate,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Update an existing agent. All fields are optional.
    Only send the fields you want to change.
    """
    return agent_service.update_agent(db, agent_id, data, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/{agent_id}",
    response_model=MessageResponse,
    status_code=200,
    summary="Delete an agent",
)
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Permanently delete an agent and all its related data.
    This includes employees, services, appointments, and conversation logs.
    This action cannot be undone.
    """
    return agent_service.delete_agent(db, agent_id, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVATE
# ─────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/{agent_id}/activate",
    response_model=AgentResponse,
    status_code=200,
    summary="Activate an agent",
)
def activate_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Activate an agent so it can start handling calls and SMS.
    """
    return agent_service.activate_agent(db, agent_id, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# DEACTIVATE
# ─────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/{agent_id}/deactivate",
    response_model=AgentResponse,
    status_code=200,
    summary="Deactivate an agent",
)
def deactivate_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Deactivate an agent so it stops handling calls and SMS.
    """
    return agent_service.deactivate_agent(db, agent_id, current_business.id)