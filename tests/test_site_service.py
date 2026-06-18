"""Regression tests for site_service config validation + publish."""

import pytest

from app.core.exceptions import AppException
from app.services import site_service


def _cfg(**over):
    base = {"schemaVersion": 1, "brand": {}, "seo": {},
            "sections": [{"id": "a", "type": "hero", "settings": {"heading": "hi"}}]}
    base.update(over)
    return base


def test_validate_accepts_good_config():
    site_service._validate_config(_cfg())  # must not raise


@pytest.mark.parametrize("bad", [
    {},                                                          # missing schemaVersion
    {"schemaVersion": "1"},                                      # wrong type
    {"schemaVersion": 1, "sections": "nope"},                   # sections not a list
    {"schemaVersion": 1, "sections": [{"type": "hero", "settings": {}}]},     # missing id
    {"schemaVersion": 1, "sections": [{"id": "a", "settings": {}}]},          # missing type
    {"schemaVersion": 1, "sections": [{"id": "a", "type": "hero", "settings": []}]},  # settings not object
    {"schemaVersion": 1, "brand": "x", "sections": []},         # brand not object
])
def test_validate_rejects_bad_structure(bad):
    with pytest.raises(AppException):
        site_service._validate_config(bad)


def test_validate_rejects_too_many_sections():
    cfg = _cfg(sections=[{"id": str(i), "type": "hero", "settings": {}} for i in range(site_service.MAX_SECTIONS + 1)])
    with pytest.raises(AppException):
        site_service._validate_config(cfg)


def test_validate_rejects_external_asset_host():
    cfg = _cfg(sections=[{"id": "a", "type": "hero", "settings": {"imageUrl": "https://evil.example.com/x.png"}}])
    with pytest.raises(AppException):
        site_service._validate_config(cfg)


def test_validate_allows_relative_asset_url():
    cfg = _cfg(sections=[{"id": "a", "type": "gallery", "settings": {"images": ["/uploads/sites/1/x.webp"]}}])
    site_service._validate_config(cfg)  # must not raise


def test_publish_copies_draft_to_published(db, bookable_branch, monkeypatch):
    # Don't hit the network for revalidation.
    monkeypatch.setattr(site_service, "_revalidate", lambda slug: False)
    aid = bookable_branch.agent_id

    site_service.update_draft(db, aid, _cfg(theme="salon-modern",
                                            sections=[{"id": "a", "type": "hero", "settings": {"heading": "Hello"}}]),
                              theme_id="salon-modern")
    site, _ = site_service.publish(db, aid)

    assert site.published_config is not None
    assert site.published_config["sections"][0]["settings"]["heading"] == "Hello"
    assert site.published_version == 1
    assert site.published_at is not None


def test_update_draft_rejects_invalid(db, bookable_branch):
    with pytest.raises(AppException):
        site_service.update_draft(db, bookable_branch.agent_id, {"schemaVersion": 1, "sections": "nope"})


def test_preview_token_roundtrip_bound_to_agent():
    token = site_service.issue_preview_token(123)
    assert site_service.verify_preview_token(token) == 123
    assert site_service.verify_preview_token("garbage") is None
