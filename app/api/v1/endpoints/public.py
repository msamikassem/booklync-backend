"""
app/api/v1/endpoints/public.py

Public booking page endpoints — no authentication required.

These are read-only endpoints used by the customer-facing booking page.
Anyone with the slug URL can access them.
"""

import socket

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.models.models import AIAgent, Service
from app.core.exceptions import NotFoundException

router = APIRouter()


def _local_network_ip() -> str:
    """Return the machine's LAN IP — used so QR codes work on mobile during dev."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


@router.get("/network-ip", include_in_schema=False)
def get_network_ip():
    """Dev-only: returns the server's LAN IP so QR codes work on mobile."""
    return {"ip": _local_network_ip(), "port": 8000}


# ── Response schemas ───────────────────────────────────────────────────────────

class PublicAgentResponse(BaseModel):
    agent_name: str
    location_name: str
    phone_number: str
    booking_page_slug: str
    business_name: str
    timezone: str

    model_config = {"from_attributes": True}


class PublicServiceResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    duration_minutes: int
    price: float

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/business/{slug}",
    response_model=PublicAgentResponse,
    summary="Get public branch info by booking page slug (no auth)",
)
def get_public_business(slug: str, db: Session = Depends(get_db)):
    agent = (
        db.query(AIAgent)
        .filter(AIAgent.booking_page_slug == slug)
        .first()
    )

    if not agent:
        raise NotFoundException(
            detail=f"No booking page found at {slug}",
            code="AGENT_NOT_FOUND",
        )

    return PublicAgentResponse(
        agent_name=agent.agent_name,
        location_name=agent.location_name,
        phone_number=agent.phone_number,
        booking_page_slug=agent.booking_page_slug,
        business_name=agent.business.name,
        timezone=agent.timezone,
    )


@router.get(
    "/business-by-domain/{hostname}",
    response_model=PublicAgentResponse,
    summary="Get public branch info by verified custom domain (no auth)",
)
def get_public_business_by_domain(hostname: str, db: Session = Depends(get_db)):
    """
    Used by book.html when loaded on a custom domain.
    Only returns data for domains that have been verified.
    """
    agent = (
        db.query(AIAgent)
        .filter(
            AIAgent.custom_domain == hostname.lower().strip(),
            AIAgent.domain_verified == True,
        )
        .first()
    )
    if not agent:
        raise NotFoundException(
            detail=f"No verified booking page found for domain {hostname}",
            code="DOMAIN_NOT_FOUND",
        )
    return PublicAgentResponse(
        agent_name=agent.agent_name,
        location_name=agent.location_name,
        phone_number=agent.phone_number,
        booking_page_slug=agent.booking_page_slug,
        business_name=agent.business.name,
        timezone=agent.timezone,
    )


@router.get(
    "/business/{slug}/services",
    response_model=list[PublicServiceResponse],
    summary="List active services for a branch (no auth)",
)
def get_public_services(slug: str, db: Session = Depends(get_db)):
    agent = (
        db.query(AIAgent)
        .filter(AIAgent.booking_page_slug == slug)
        .first()
    )

    if not agent:
        return []

    services = (
        db.query(Service)
        .filter(Service.agent_id == agent.id, Service.active == True)
        .all()
    )

    return [
        PublicServiceResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            duration_minutes=s.duration_minutes,
            price=float(s.price),
        )
        for s in services
    ]
