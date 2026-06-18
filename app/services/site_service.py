"""
app/services/site_service.py

Business logic for the website builder (BranchSite).

Design notes:
- The site config is a (mostly) opaque JSON document. Section *definitions* live
  in the frontend, so this layer only guards **structure** (valid object, known
  top-level shape, sane caps) — never section types or per-section settings. That
  keeps "add a new section/theme" a frontend-only change.
- Two copies per branch: `draft_config` (editor) and `published_config` (live).
  Editing never touches the live site until publish().
- Publishing asks the Next app to re-render the live page (best-effort), so a
  publish is visible within seconds rather than waiting out the ISR window.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from jose import JWTError, jwt
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.models.models import AIAgent, BranchSite

# Current config schema. Stamped into every default config; the renderer keys
# forward-compat behaviour off it.
SCHEMA_VERSION = 1

# Structural guard-rails (NOT a content policy — just abuse/footgun limits).
MAX_CONFIG_BYTES = 256 * 1024   # 256 KB serialized
MAX_SECTIONS = 50

_PREVIEW_TOKEN_TYPE = "site_preview"


# ── Defaults ────────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    """A blank, theme-less site. The editor shows the theme gallery until the
    owner picks one (which PATCHes a full theme preset over this)."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "theme": None,
        "brand": {},
        "seo": {},
        "sections": [],
    }


# ── Draft read / write ───────────────────────────────────────────────────────────

def get_or_create_draft(db: Session, agent_id: int) -> BranchSite:
    """Return the branch's site row, creating an empty draft on first access."""
    site = db.query(BranchSite).filter(BranchSite.agent_id == agent_id).first()
    if site:
        return site

    site = BranchSite(agent_id=agent_id, draft_config=_default_config())
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


def update_draft(
    db: Session,
    agent_id: int,
    config: dict,
    theme_id: Optional[str] = None,
) -> BranchSite:
    """Replace the whole draft config. The editor owns the document."""
    _validate_config(config)

    site = get_or_create_draft(db, agent_id)
    site.draft_config = config
    flag_modified(site, "draft_config")  # JSONB reassignment needs the flag too
    if theme_id is not None:
        site.theme_id = theme_id
    db.commit()
    db.refresh(site)
    return site


def publish(db: Session, agent_id: int) -> tuple[BranchSite, bool]:
    """Copy draft → published, bump the version, and ask the live page to
    re-render. Returns (site, revalidated)."""
    site = db.query(BranchSite).filter(BranchSite.agent_id == agent_id).first()
    if not site:
        raise NotFoundException(detail="No site to publish", code="SITE_NOT_FOUND")

    _validate_config(site.draft_config)

    # Deep-copy via JSON round-trip so the published snapshot is independent of
    # later draft edits (and definitely a fresh object for flag_modified).
    site.published_config = json.loads(json.dumps(site.draft_config))
    flag_modified(site, "published_config")
    site.published_version = (site.published_version or 0) + 1
    site.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(site)

    revalidated = _revalidate(site.agent.booking_page_slug) if site.agent else False
    return site, revalidated


# ── Validation (structure only) ──────────────────────────────────────────────────

def _validate_config(config) -> None:
    """Guard the document's *shape* only — never section types or settings."""
    if not isinstance(config, dict):
        raise AppException(detail="Site config must be an object", code="INVALID_SITE_CONFIG")

    if not isinstance(config.get("schemaVersion"), int):
        raise AppException(
            detail="Site config is missing a valid schemaVersion",
            code="INVALID_SITE_CONFIG",
        )

    sections = config.get("sections", [])
    if not isinstance(sections, list):
        raise AppException(detail="Site sections must be a list", code="INVALID_SITE_CONFIG")
    if len(sections) > MAX_SECTIONS:
        raise AppException(
            detail=f"A site can have at most {MAX_SECTIONS} sections",
            code="TOO_MANY_SECTIONS",
        )

    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            raise AppException(detail=f"Section {i} must be an object", code="INVALID_SITE_CONFIG")
        if not isinstance(section.get("type"), str) or not section["type"]:
            raise AppException(detail=f"Section {i} is missing a type", code="INVALID_SITE_CONFIG")
        if not isinstance(section.get("id"), str) or not section["id"]:
            raise AppException(detail=f"Section {i} is missing an id", code="INVALID_SITE_CONFIG")
        if "settings" in section and not isinstance(section["settings"], dict):
            raise AppException(
                detail=f"Section {i} settings must be an object",
                code="INVALID_SITE_CONFIG",
            )

    for key in ("brand", "seo"):
        if key in config and not isinstance(config[key], dict):
            raise AppException(detail=f"Site {key} must be an object", code="INVALID_SITE_CONFIG")

    size = len(json.dumps(config).encode("utf-8"))
    if size > MAX_CONFIG_BYTES:
        raise AppException(
            detail=f"Site config is too large ({size} bytes; max {MAX_CONFIG_BYTES})",
            code="SITE_CONFIG_TOO_LARGE",
        )

    _validate_asset_urls(config)


# Image fields whose URLs must point at our own storage (owners only ever get
# these from our uploader). Non-image URL fields (e.g. future social links) are
# intentionally NOT restricted. Keep in sync as image-bearing sections are added.
_ASSET_URL_KEYS = {"logoUrl", "faviconUrl", "ogImageUrl", "imageUrl", "backgroundImageUrl"}
_ASSET_LIST_KEYS = {"images"}


def _allowed_asset_hosts() -> set[str]:
    hosts: set[str] = set()
    for url in (settings.API_BASE_URL, settings.WEB_APP_URL, settings.SUPABASE_URL):
        h = urlparse(url).hostname if url else None
        if h:
            hosts.add(h)
    return hosts


def _check_asset_url(value, allowed: set[str]) -> None:
    if not isinstance(value, str) or not value:
        return
    if value.startswith(("http://", "https://")):
        host = (urlparse(value).hostname or "").lower()
        if host not in allowed:
            raise AppException(
                detail=f"Image URL is not on an allowed host: {host}",
                code="ASSET_HOST_NOT_ALLOWED",
            )
    # relative URLs (e.g. "/uploads/...") are fine — same origin


def _validate_asset_urls(node, allowed: Optional[set[str]] = None) -> None:
    """Recursively ensure image fields reference our own storage host."""
    if allowed is None:
        allowed = _allowed_asset_hosts()
    if isinstance(node, dict):
        for key, val in node.items():
            if key in _ASSET_URL_KEYS:
                _check_asset_url(val, allowed)
            elif key in _ASSET_LIST_KEYS and isinstance(val, list):
                for item in val:
                    _check_asset_url(item, allowed)
            else:
                _validate_asset_urls(val, allowed)
    elif isinstance(node, list):
        for item in node:
            _validate_asset_urls(item, allowed)


# ── Preview tokens ───────────────────────────────────────────────────────────────

def issue_preview_token(agent_id: int) -> str:
    """Short-lived token letting the (unauthenticated) preview route read this
    branch's DRAFT. Bound to the agent so it can't be replayed against another."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.PREVIEW_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(agent_id), "type": _PREVIEW_TOKEN_TYPE, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_preview_token(token: str) -> Optional[int]:
    """Return the agent_id a valid preview token authorises, else None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") != _PREVIEW_TOKEN_TYPE:
        return None
    sub = payload.get("sub")
    try:
        return int(sub) if sub is not None else None
    except (TypeError, ValueError):
        return None


# ── Revalidation (publish → live re-render) ──────────────────────────────────────

def _revalidate(slug: Optional[str]) -> bool:
    """Ask the Next app to re-render the published site for `slug`. Best-effort:
    a failure never blocks a publish (the page still refreshes on its own ISR
    interval). Returns True only on a confirmed 2xx."""
    if not slug or not settings.REVALIDATE_SECRET:
        return False
    try:
        import httpx
        res = httpx.post(
            f"{settings.WEB_APP_URL}/api/revalidate",
            headers={"x-revalidate-secret": settings.REVALIDATE_SECRET},
            json={"slug": slug},
            timeout=5,
        )
        if res.status_code // 100 == 2:
            return True
        logger.warning(f"Site revalidate for '{slug}' returned {res.status_code}: {res.text}")
        return False
    except Exception as exc:  # network/refused — non-fatal
        logger.warning(f"Site revalidate for '{slug}' failed: {exc}")
        return False
