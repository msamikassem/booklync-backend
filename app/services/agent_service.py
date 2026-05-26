"""
app/services/agent_service.py

All business logic for AI agent management.

Key rules enforced here:
  - A business can only access its own agents
  - Phone numbers must be unique across all agents
  - An agent must belong to the logged in business to be modified
  - Deleting an agent cascades to employees, services, appointments etc
"""

import re

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import AIAgent, Business
from app.schemas.agent import AgentCreate, AgentUpdate
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ForbiddenException,
)


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────

def create_agent(db: Session, data: AgentCreate, business_id: int) -> AIAgent:
    """
    Create a new AI agent for the logged in business.

    Steps:
    1. Check phone number not already taken by another agent
    2. Create the agent record
    3. Return the new agent

    Args:
        db:          database session
        data:        validated AgentCreate schema
        business_id: id of the logged in business

    Returns:
        AIAgent SQLAlchemy object

    Raises:
        ConflictException: if phone number already in use
    """

    # Step 1 — check phone number is not already taken
    existing = db.query(AIAgent).filter(
        AIAgent.phone_number == data.phone_number
    ).first()

    if existing:
        raise ConflictException(
            detail="An agent with this phone number already exists",
            code="PHONE_NUMBER_TAKEN"
        )

    # Generate unique slug from location_name
    base_slug = re.sub(r'[^a-z0-9]+', '-', data.location_name.lower()).strip('-') or 'branch'
    slug = base_slug
    counter = 2
    while db.query(AIAgent).filter(AIAgent.booking_page_slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Step 2 — create the agent
    agent = AIAgent(
        business_id=business_id,
        agent_name=data.agent_name,
        phone_number=data.phone_number,
        location_name=data.location_name,
        timezone=data.timezone,
        model=data.model,
        sms_welcome_message=data.sms_welcome_message,
        sms_post_welcome_behavior=data.sms_post_welcome_behavior,
        booking_page_slug=slug,
        active=False,    # agents start inactive until configured and ready
    )

    db.add(agent)
    db.commit()
    db.refresh(agent)

    logger.info(f"Agent created: {agent.agent_name} (id={agent.id}) for business_id={business_id}")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# GET ALL
# ─────────────────────────────────────────────────────────────────────────────

def get_agents(db: Session, business_id: int, restrict_to: set[int] | None = None) -> dict:
    """
    Get agents belonging to the logged in business.

    A business can only see its own agents — never another business's agents.
    When `restrict_to` is provided (non-owner users), the result is further
    limited to those branch ids, so a branch-scoped user only sees the branches
    they are assigned to. Owners pass None and see every branch.

    Args:
        db:          database session
        business_id: id of the logged in business
        restrict_to: optional set of agent ids the caller may see

    Returns:
        dict with agents list and total count
    """
    query = db.query(AIAgent).filter(AIAgent.business_id == business_id)
    if restrict_to is not None:
        query = query.filter(AIAgent.id.in_(restrict_to))
    agents = query.all()

    return {
        "agents": agents,
        "total": len(agents),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET ONE
# ─────────────────────────────────────────────────────────────────────────────

def get_agent(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """
    Get one specific agent by id.

    Verifies the agent belongs to the logged in business.
    A business cannot access another business's agents.

    Args:
        db:          database session
        agent_id:    id of the agent to fetch
        business_id: id of the logged in business

    Returns:
        AIAgent SQLAlchemy object

    Raises:
        NotFoundException: if agent not found
        ForbiddenException: if agent belongs to a different business
    """

    agent = db.query(AIAgent).filter(
        AIAgent.id == agent_id
    ).first()

    if not agent:
        raise NotFoundException(detail="Agent not found")

    # security check — make sure this agent belongs to the logged in business
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update_agent(db: Session, agent_id: int, data: AgentUpdate, business_id: int) -> AIAgent:
    """
    Update an existing agent.

    Only updates fields that are actually sent — fields set to None are skipped.
    This allows partial updates: send only what you want to change.

    Args:
        db:          database session
        agent_id:    id of the agent to update
        data:        validated AgentUpdate schema (all fields optional)
        business_id: id of the logged in business

    Returns:
        Updated AIAgent SQLAlchemy object

    Raises:
        NotFoundException:  if agent not found
        ForbiddenException: if agent belongs to a different business
        ConflictException:  if new phone number already in use
    """

    # fetch and verify ownership
    agent = get_agent(db, agent_id, business_id)

    # if phone number is being changed, check it's not taken by another agent
    if data.phone_number and data.phone_number != agent.phone_number:
        existing = db.query(AIAgent).filter(
            AIAgent.phone_number == data.phone_number
        ).first()
        if existing:
            raise ConflictException(
                detail="An agent with this phone number already exists",
                code="PHONE_NUMBER_TAKEN"
            )

    # update only the fields that were actually sent
    # data.model_dump(exclude_none=True) returns a dict of only non-None fields
    update_data = data.model_dump(exclude_none=True)

    for field, value in update_data.items():
        setattr(agent, field, value)
        # setattr(agent, "agent_name", "New Name")
        # is the same as agent.agent_name = "New Name"
        # but works dynamically for any field name

    db.commit()
    db.refresh(agent)

    logger.info(f"Agent updated: id={agent_id} by business_id={business_id}")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────────────────────────────────────

def delete_agent(db: Session, agent_id: int, business_id: int) -> dict:
    """
    Delete an agent and all its related data.

    Cascade deletes (defined in models.py) automatically remove:
    - employees
    - business hours
    - services
    - appointments
    - conversation logs
    - subscriptions
    - features
    - days off

    Args:
        db:          database session
        agent_id:    id of the agent to delete
        business_id: id of the logged in business

    Returns:
        dict with success message

    Raises:
        NotFoundException:  if agent not found
        ForbiddenException: if agent belongs to a different business
    """

    agent = get_agent(db, agent_id, business_id)

    db.delete(agent)
    db.commit()

    logger.info(f"Agent deleted: id={agent_id} by business_id={business_id}")

    return {"message": f"Agent '{agent.agent_name}' deleted successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVATE / DEACTIVATE
# ─────────────────────────────────────────────────────────────────────────────

def activate_agent(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """
    Activate an agent so it can start handling calls and SMS.

    Args:
        db:          database session
        agent_id:    id of the agent to activate
        business_id: id of the logged in business

    Returns:
        Updated AIAgent object
    """
    agent = get_agent(db, agent_id, business_id)

    agent.active = True
    db.commit()
    db.refresh(agent)

    logger.info(f"Agent activated: id={agent_id} by business_id={business_id}")

    return agent


def deactivate_agent(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """
    Deactivate an agent so it stops handling calls and SMS.

    Args:
        db:          database session
        agent_id:    id of the agent to deactivate
        business_id: id of the logged in business

    Returns:
        Updated AIAgent object
    """
    agent = get_agent(db, agent_id, business_id)

    agent.active = False
    db.commit()
    db.refresh(agent)

    logger.info(f"Agent deactivated: id={agent_id} by business_id={business_id}")

    return agent