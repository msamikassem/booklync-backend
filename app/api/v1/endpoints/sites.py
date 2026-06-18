"""
app/api/v1/endpoints/sites.py

Website-builder routes (authenticated). The owner/manager edits a branch's site
here; the public render lives in endpoints/public.py.

All routes are gated by `manage_branches` at the target branch (owners
short-circuit). Tenant + branch access is enforced by `current.require(...)`,
which also asserts the agent is in-tenant — so the `business_id` never comes from
the body (load-bearing rule).
"""

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core import rbac
from app.core.config import settings
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import NotFoundException, AppException
from app.models.models import AIAgent, BranchSite
from app.schemas.site import (
    SiteDraftResponse,
    SiteDraftUpdate,
    SitePublishResponse,
    PreviewTokenResponse,
    SiteAssetResponse,
)
from app.services import site_service, storage_service

router = APIRouter()

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon",
}


def _get_agent(db: Session, agent_id: int, current: CurrentUser) -> AIAgent:
    """Authorise the branch + return its agent row (needed for the slug)."""
    current.require(rbac.P_MANAGE_BRANCHES, agent_id=agent_id)
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Branch not found", code="AGENT_NOT_FOUND")
    return agent


def _to_draft_response(site: BranchSite) -> SiteDraftResponse:
    return SiteDraftResponse(
        agent_id=site.agent_id,
        theme_id=site.theme_id,
        draft_config=site.draft_config,
        has_published=site.published_config is not None,
        published_version=site.published_version,
        published_at=site.published_at,
        branding_removed=site.branding_removed,
    )


@router.get(
    "/{agent_id}/draft",
    response_model=SiteDraftResponse,
    summary="Get the editable site draft for a branch (creates a blank one on first access)",
)
def get_draft(
    agent_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    _get_agent(db, agent_id, current)
    site = site_service.get_or_create_draft(db, agent_id)
    return _to_draft_response(site)


@router.patch(
    "/{agent_id}/draft",
    response_model=SiteDraftResponse,
    summary="Replace the site draft config",
)
def patch_draft(
    agent_id: int,
    data: SiteDraftUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    _get_agent(db, agent_id, current)
    site = site_service.update_draft(db, agent_id, data.config, data.theme_id)
    return _to_draft_response(site)


@router.post(
    "/{agent_id}/publish",
    response_model=SitePublishResponse,
    summary="Publish the draft → live site",
)
def publish_site(
    agent_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    _get_agent(db, agent_id, current)
    site, revalidated = site_service.publish(db, agent_id)
    return SitePublishResponse(
        published_version=site.published_version,
        published_at=site.published_at,
        revalidated=revalidated,
    )


@router.get(
    "/{agent_id}/preview-token",
    response_model=PreviewTokenResponse,
    summary="Mint a short-lived token for the editor's live draft preview",
)
def get_preview_token(
    agent_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    agent = _get_agent(db, agent_id, current)
    token = site_service.issue_preview_token(agent_id)
    slug = agent.booking_page_slug or ""
    return PreviewTokenResponse(
        token=token,
        preview_path=f"/preview/{slug}?token={token}",
        expires_in_minutes=settings.PREVIEW_TOKEN_EXPIRE_MINUTES,
    )


@router.post(
    "/{agent_id}/assets",
    response_model=SiteAssetResponse,
    summary="Upload a site image (logo, hero, gallery). Returns its public URL.",
)
async def upload_asset(
    agent_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    _get_agent(db, agent_id, current)
    if (file.content_type or "").lower() not in _ALLOWED_IMAGE_TYPES:
        raise AppException(
            detail="Image must be a JPEG, PNG, WebP, GIF, SVG, or ICO file",
            code="INVALID_IMAGE_TYPE",
            status_code=415,
        )
    content = await file.read()
    url = storage_service.upload_site_asset(content, file.filename or "", agent_id)
    return SiteAssetResponse(url=url)
