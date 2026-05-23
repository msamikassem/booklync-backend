"""
app/api/v1/endpoints/agents.py

AI agent management routes.

All endpoints here are protected — require a valid JWT token.
A business can only manage its own agents.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
import httpx

from app.db.session import get_db
from app.core.config import settings
from app.core.dependencies import get_current_business
from app.core.exceptions import NotFoundException, ConflictException
from app.models.models import AIAgent, Business
from app.schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    AgentListResponse,
    MessageResponse,
)
from app.services import agent_service


# ── Custom domain schemas ─────────────────────────────────────────────────────

class CustomDomainRequest(BaseModel):
    domain: str   # e.g. "book.mysalon.com"

class CustomDomainResponse(BaseModel):
    domain: str
    cname_target: str
    verified: bool

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


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM DOMAIN — connect / verify / remove
# ─────────────────────────────────────────────────────────────────────────────

def _get_owned_agent(db: Session, agent_id: int, business_id: int) -> AIAgent:
    agent = db.query(AIAgent).filter(
        AIAgent.id == agent_id,
        AIAgent.business_id == business_id,
    ).first()
    if not agent:
        raise NotFoundException(detail="Agent not found", code="AGENT_NOT_FOUND")
    return agent


def _vercel_add_domain(domain: str) -> None:
    """Register a domain with Vercel so it provisions SSL. No-op when token is unset."""
    if not settings.VERCEL_TOKEN or not settings.VERCEL_PROJECT_ID:
        return
    params = {"teamId": settings.VERCEL_TEAM_ID} if settings.VERCEL_TEAM_ID else {}
    httpx.post(
        f"https://api.vercel.com/v10/projects/{settings.VERCEL_PROJECT_ID}/domains",
        headers={"Authorization": f"Bearer {settings.VERCEL_TOKEN}"},
        json={"name": domain},
        params=params,
        timeout=10,
    )


def _vercel_remove_domain(domain: str) -> None:
    """Remove a domain from Vercel. No-op when token is unset."""
    if not settings.VERCEL_TOKEN or not settings.VERCEL_PROJECT_ID:
        return
    params = {"teamId": settings.VERCEL_TEAM_ID} if settings.VERCEL_TEAM_ID else {}
    httpx.delete(
        f"https://api.vercel.com/v9/projects/{settings.VERCEL_PROJECT_ID}/domains/{domain}",
        headers={"Authorization": f"Bearer {settings.VERCEL_TOKEN}"},
        params=params,
        timeout=10,
    )


def _check_dns(domain: str) -> bool:
    """
    Return True if the domain's DNS has propagated.
    Uses Cloudflare's DNS-over-HTTPS so no extra dependencies are needed.
    Checks for CNAME first, then A record (covers both subdomains and apex domains).
    """
    try:
        for record_type in ("CNAME", "A"):
            res = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": domain, "type": record_type},
                headers={"Accept": "application/dns-json"},
                timeout=8,
            )
            if res.status_code == 200:
                data = res.json()
                if data.get("Status") == 0 and data.get("Answer"):
                    return True
    except Exception:
        pass
    return False


@router.post(
    "/{agent_id}/custom-domain",
    response_model=CustomDomainResponse,
    status_code=200,
    summary="Connect a custom domain to this agent's booking page",
)
def connect_custom_domain(
    agent_id: int,
    body: CustomDomainRequest,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    domain = body.domain.lower().strip().rstrip("/")
    agent  = _get_owned_agent(db, agent_id, current_business.id)

    # Reject if already taken by another agent
    conflict = db.query(AIAgent).filter(
        AIAgent.custom_domain == domain,
        AIAgent.id != agent_id,
    ).first()
    if conflict:
        raise ConflictException(
            detail="That domain is already connected to another booking page.",
            code="DOMAIN_TAKEN",
        )

    agent.custom_domain   = domain
    agent.domain_verified = False
    db.commit()

    _vercel_add_domain(domain)

    return CustomDomainResponse(
        domain=domain,
        cname_target=settings.CNAME_TARGET,
        verified=False,
    )


@router.post(
    "/{agent_id}/custom-domain/verify",
    response_model=CustomDomainResponse,
    status_code=200,
    summary="Verify DNS propagation for the connected custom domain",
)
def verify_custom_domain(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
    force: bool = Query(False, description="Skip DNS check — dev/testing only"),
):
    agent = _get_owned_agent(db, agent_id, current_business.id)

    if not agent.custom_domain:
        raise NotFoundException(detail="No custom domain connected.", code="NO_CUSTOM_DOMAIN")

    dns_ok = force and settings.APP_ENV == "development" or _check_dns(agent.custom_domain)
    if dns_ok:
        agent.domain_verified = True
        db.commit()

    return CustomDomainResponse(
        domain=agent.custom_domain,
        cname_target=settings.CNAME_TARGET,
        verified=agent.domain_verified,
    )


@router.delete(
    "/{agent_id}/custom-domain",
    response_model=MessageResponse,
    status_code=200,
    summary="Remove the custom domain from this agent's booking page",
)
def remove_custom_domain(
    agent_id: int,
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    agent  = _get_owned_agent(db, agent_id, current_business.id)
    domain = agent.custom_domain

    agent.custom_domain   = None
    agent.domain_verified = False
    db.commit()

    if domain:
        _vercel_remove_domain(domain)

    return MessageResponse(message="Custom domain removed.")