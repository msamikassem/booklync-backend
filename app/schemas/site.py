"""
app/schemas/site.py

Pydantic schemas for the website builder (BranchSite).

The site config itself is a flexible JSON document — section *definitions* live in
the frontend (TypeScript), so the backend keeps the config loosely typed
(`dict`) and only guards its structure (see site_service._validate_config). These
schemas describe the API envelope around that document, not the document shape.
"""

from typing import Any, Optional
from datetime import datetime

from pydantic import BaseModel

# A site config is a free-form JSON object. Typed as a plain dict on purpose.
SiteConfig = dict[str, Any]


# ── Authenticated editor schemas ────────────────────────────────────────────────

class SiteDraftResponse(BaseModel):
    """The editor's view of a branch site (draft + publish status)."""
    agent_id: int
    theme_id: Optional[str] = None
    draft_config: SiteConfig
    has_published: bool
    published_version: int
    published_at: Optional[datetime] = None
    branding_removed: bool

    model_config = {"from_attributes": True}


class SiteDraftUpdate(BaseModel):
    """PATCH body — replaces the whole draft config (the editor owns the document)."""
    config: SiteConfig
    # Optional: record which theme the draft was seeded from (informational).
    theme_id: Optional[str] = None


class SitePublishResponse(BaseModel):
    published_version: int
    published_at: datetime
    # True if the live page was successfully asked to re-render (best-effort).
    revalidated: bool


class PreviewTokenResponse(BaseModel):
    token: str
    # Convenience: the full preview URL the editor iframe should load.
    preview_path: str
    expires_in_minutes: int


class SiteAssetResponse(BaseModel):
    url: str


# ── Public (unauthenticated) schemas ────────────────────────────────────────────

class PublicSiteBranch(BaseModel):
    """Branch identity for the public renderer (mirrors PublicAgentResponse).
    `agent_id` is needed by the booking widget to call the (already public)
    availability + booking endpoints — it is not a secret."""
    agent_id: int
    agent_name: str
    location_name: str
    phone_number: str
    booking_page_slug: str
    business_name: str
    timezone: str


class PublicSiteVariation(BaseModel):
    id: int
    name: str
    duration_minutes: Optional[int] = None
    price: Optional[float] = None


class PublicSiteService(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    # Bookable options for a variation-based service (empty for flat-rate). The
    # booking widget books a specific variation when these are present.
    variations: list[PublicSiteVariation] = []


class PublicBookingRequest(BaseModel):
    """Customer-submitted booking from a published site (unauthenticated).
    Tenant/branch are resolved from `slug` — never trusted from the body."""
    slug: str
    service_id: int
    variation_id: Optional[int] = None
    employee_id: int
    start_time: datetime
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    note: Optional[str] = None
    # Honeypot: bots fill hidden fields. Real submissions leave it blank.
    hp: Optional[str] = None
    # Cloudflare Turnstile token (only required when the server has TURNSTILE_SECRET).
    turnstile_token: Optional[str] = None


class PublicBookingConfirmation(BaseModel):
    status: str
    start_time: datetime
    start_time_local: Optional[str] = None
    service_name: Optional[str] = None
    employee_name: Optional[str] = None
    branch_name: str
    # True when the booking awaits staff confirmation (the default for public
    # bookings). The site shows a "we'll confirm shortly" message.
    pending: bool


class PublicSiteResponse(BaseModel):
    """
    Everything the public renderer needs in ONE round trip: branch identity, the
    live service menu, and the published (or, in preview, draft) site config.

    `published` is False when the branch has never published a site — the
    frontend then falls back to the simple built-in booking page.
    """
    branch: PublicSiteBranch
    services: list[PublicSiteService]
    config: Optional[SiteConfig] = None
    published: bool
    branding_removed: bool
    # True only on the preview endpoint (draft content, never cache).
    preview: bool = False
