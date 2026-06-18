"""
app/api/v1/endpoints/public.py

Public booking page endpoints — no authentication required.

These are read-only endpoints used by the customer-facing booking page.
Anyone with the slug URL can access them.
"""

import socket
import time as _time
from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.config import settings
from app.models.models import AIAgent, BranchSite, Service, ServiceVariation
from app.core.exceptions import NotFoundException, AppException
from app.schemas.site import (
    PublicSiteResponse,
    PublicSiteBranch,
    PublicSiteService,
    PublicBookingRequest,
    PublicBookingConfirmation,
)
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentSourceEnum,
    AppointmentStatusEnum,
)
from app.services import site_service, appointment_service, customer_service

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
    # Nullable: a variation-based service has no flat price/duration of its own —
    # we surface the cheapest active variation as the "from" price/duration.
    duration_minutes: Optional[int] = None
    price: Optional[float] = None

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


def _serialize_services(db: Session, agent: AIAgent, include_variations: bool = False) -> list[dict]:
    """Active services for a branch as plain dicts.

    `include_variations` adds each variation-based service's bookable options (used
    by the website-builder site endpoints + booking widget). The legacy services
    endpoint leaves it off, so its payload shape is unchanged."""
    services = (
        db.query(Service)
        .filter(Service.agent_id == agent.id, Service.active == True)
        .all()
    )

    out: list[dict] = []
    for s in services:
        price = s.price
        duration = s.duration_minutes
        variations = (
            db.query(ServiceVariation)
            .filter(ServiceVariation.service_id == s.id, ServiceVariation.active == True)
            .order_by(ServiceVariation.price.asc())
            .all()
        )
        # Variation-based service: flat price/duration are NULL — fall back to the
        # cheapest active variation so the public menu shows a "from" price.
        if (price is None or duration is None) and variations:
            cheapest = variations[0]
            if price is None:
                price = cheapest.price
            if duration is None:
                duration = cheapest.duration_minutes
        row = {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "duration_minutes": duration,
            "price": float(price) if price is not None else None,
        }
        if include_variations:
            row["variations"] = [
                {
                    "id": v.id,
                    "name": v.name,
                    "duration_minutes": v.duration_minutes,
                    "price": float(v.price) if v.price is not None else None,
                }
                for v in variations
            ]
        out.append(row)
    return out


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

    return [PublicServiceResponse(**s) for s in _serialize_services(db, agent)]


@router.get(
    "/domain-slug/{hostname}",
    summary="Resolve a verified custom domain → its branch slug (no auth)",
)
def get_domain_slug(hostname: str, db: Session = Depends(get_db)):
    """Tiny lookup so `middleware.ts` can rewrite a custom domain onto the
    ISR-cached `/b/{slug}` renderer (instead of an uncached per-request resolve).
    Verified domains only."""
    agent = (
        db.query(AIAgent)
        .filter(
            AIAgent.custom_domain == hostname.lower().strip(),
            AIAgent.domain_verified == True,
        )
        .first()
    )
    if not agent or not agent.booking_page_slug:
        raise NotFoundException(detail=f"No verified domain {hostname}", code="DOMAIN_NOT_FOUND")
    return {"slug": agent.booking_page_slug}


# ── Website builder (public render) ──────────────────────────────────────────────

def _public_branch(agent: AIAgent) -> PublicSiteBranch:
    return PublicSiteBranch(
        agent_id=agent.id,
        agent_name=agent.agent_name,
        location_name=agent.location_name,
        phone_number=agent.phone_number,
        booking_page_slug=agent.booking_page_slug,
        business_name=agent.business.name,
        timezone=agent.timezone,
    )


@router.get(
    "/site/{slug}",
    response_model=PublicSiteResponse,
    summary="Get a branch's PUBLISHED website + identity + services in one call (no auth)",
)
def get_public_site(slug: str, db: Session = Depends(get_db)):
    """One round trip for the public renderer. `published=False` (config null)
    means the branch has no live site yet → the frontend falls back to the
    built-in booking page."""
    agent = db.query(AIAgent).filter(AIAgent.booking_page_slug == slug).first()
    if not agent:
        raise NotFoundException(detail=f"No booking page found at {slug}", code="AGENT_NOT_FOUND")

    site = agent.site
    config = site.published_config if site else None
    return PublicSiteResponse(
        branch=_public_branch(agent),
        services=[PublicSiteService(**s) for s in _serialize_services(db, agent, include_variations=True)],
        config=config,
        published=config is not None,
        branding_removed=bool(site.branding_removed) if site else False,
        preview=False,
    )


@router.get(
    "/site/{slug}/draft",
    response_model=PublicSiteResponse,
    summary="Get a branch's DRAFT website for the editor preview (signed token, no auth)",
)
def get_public_site_draft(
    slug: str,
    token: str = Query(..., description="Signed preview token from /sites/{id}/preview-token"),
    db: Session = Depends(get_db),
):
    agent = db.query(AIAgent).filter(AIAgent.booking_page_slug == slug).first()
    if not agent:
        raise NotFoundException(detail=f"No booking page found at {slug}", code="AGENT_NOT_FOUND")

    # Token must be valid AND minted for THIS branch (no cross-branch replay).
    token_agent_id = site_service.verify_preview_token(token)
    if token_agent_id != agent.id:
        raise NotFoundException(detail="Invalid or expired preview token", code="INVALID_PREVIEW_TOKEN")

    site = agent.site
    config = site.draft_config if site else None
    return PublicSiteResponse(
        branch=_public_branch(agent),
        services=[PublicSiteService(**s) for s in _serialize_services(db, agent, include_variations=True)],
        config=config,
        published=config is not None,
        branding_removed=bool(site.branding_removed) if site else False,
        preview=True,
    )


# ── Public booking (customer self-serve) ─────────────────────────────────────────
#
# Abuse guard for an UNAUTHENTICATED write endpoint. In-memory sliding window
# (resets on restart — same trade-off as the reminder-dedup set). Production
# hardening (Cloudflare Turnstile / phone OTP) plugs in here; see
# docs/modules/website_builder.md.

_RATE_WINDOW_SECONDS = 600          # 10 minutes
_RATE_MAX_PER_WINDOW = 5            # per IP and per phone
_rate_buckets: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    # Behind the proxy/middleware, request.client.host is the proxy — use the
    # first hop of X-Forwarded-For when present.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(keys: list[str]) -> bool:
    now = _time.time()
    cutoff = now - _RATE_WINDOW_SECONDS
    limited = False
    for key in keys:
        bucket = _rate_buckets.setdefault(key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_MAX_PER_WINDOW:
            limited = True
        bucket.append(now)
    return limited


def _verify_turnstile(token: Optional[str], ip: str) -> bool:
    """Verify a Cloudflare Turnstile token. No-op (returns True) when no secret is
    configured. siteverify is FORM-encoded (not JSON)."""
    if not settings.TURNSTILE_SECRET:
        return True
    if not token:
        return False
    try:
        import httpx
        res = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": settings.TURNSTILE_SECRET, "response": token, "remoteip": ip},
            timeout=5,
        )
        return bool(res.json().get("success"))
    except Exception:
        return False


def _normalize_phone(raw: str) -> str:
    """Single canonical form: keep a leading '+' and digits only. NOTE: with no
    country field this is best-effort — a customer who typed their number in a
    different format than the dashboard stored may create a second record. A
    proper libphonenumber pass is a later item."""
    raw = (raw or "").strip()
    plus = raw.startswith("+")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" + digits) if plus else digits


@router.post(
    "/appointments",
    response_model=PublicBookingConfirmation,
    summary="Create a booking from a published site (no auth)",
)
def create_public_appointment(
    data: PublicBookingRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # Honeypot — bots fill hidden fields; real submissions leave it blank.
    if data.hp:
        raise AppException(detail="Unable to process this booking.", code="BOOKING_REJECTED", status_code=400)

    agent = db.query(AIAgent).filter(AIAgent.booking_page_slug == data.slug).first()
    if not agent:
        raise NotFoundException(detail=f"No booking page found at {data.slug}", code="AGENT_NOT_FOUND")

    phone = _normalize_phone(data.customer_phone)
    if not phone or len("".join(c for c in phone if c.isdigit())) < 7:
        raise AppException(detail="Please enter a valid phone number.", code="INVALID_PHONE", status_code=400)
    if not (data.customer_name or "").strip():
        raise AppException(detail="Please enter your name.", code="INVALID_NAME", status_code=400)

    client_ip = _client_ip(request)
    if _rate_limited([f"ip:{client_ip}", f"phone:{agent.business_id}:{phone}"]):
        raise AppException(
            detail="Too many booking attempts. Please try again in a few minutes.",
            code="RATE_LIMITED", status_code=429,
        )

    # Bot protection (no-op unless TURNSTILE_SECRET is configured).
    if not _verify_turnstile(data.turnstile_token, client_ip):
        raise AppException(
            detail="Spam check failed. Please try again.",
            code="CAPTCHA_FAILED", status_code=400,
        )

    customer = customer_service.get_or_create_customer(
        db, phone=phone, business_id=agent.business_id,
        name=data.customer_name.strip(), email=(data.customer_email or None),
    )

    appt = appointment_service.create_appointment(
        db,
        AppointmentCreate(
            agent_id=agent.id,
            customer_id=customer.id,
            employee_id=data.employee_id,
            service_id=data.service_id,
            variation_id=data.variation_id,
            start_time=data.start_time,
            source=AppointmentSourceEnum.booking_page,
            note=data.note,
        ),
        business_id=agent.business_id,
        # Default: PENDING for staff review (no SMS to an unverified number).
        # A branch may opt into auto-confirm (sends the confirmation SMS) — only
        # recommended with Turnstile on, since auto-confirm + an open endpoint
        # could otherwise be abused to SMS arbitrary numbers.
        status=(
            AppointmentStatusEnum.confirmed
            if agent.public_booking_auto_confirm
            else AppointmentStatusEnum.pending
        ),
    )

    status_val = getattr(appt["status"], "value", str(appt["status"]))
    start_local = appt.get("start_time_local")
    return PublicBookingConfirmation(
        status=status_val,
        start_time=appt["start_time"],
        start_time_local=str(start_local) if start_local is not None else None,
        service_name=appt.get("service_name"),
        employee_name=appt.get("employee_name"),
        branch_name=agent.agent_name,
        pending=status_val == "pending",
    )
