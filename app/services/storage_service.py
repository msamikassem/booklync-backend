"""
app/services/storage_service.py

Handles file storage for profile pictures.
Supports two backends controlled by STORAGE_BACKEND in .env:
  - "local"    → saves to UPLOAD_DIR on disk, served via /uploads
  - "supabase" → uploads to Supabase Storage (bucket: profile-pictures)
"""

import os
from pathlib import Path
from fastapi import HTTPException

from app.core.config import settings

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
BUCKET_NAME = "profile-pictures"


def upload_profile_picture(content: bytes, filename: str, employee_id: int) -> str:
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 2MB.")

    ext = _get_extension(filename)

    if settings.STORAGE_BACKEND == "supabase":
        return _upload_to_supabase(content, ext, employee_id)
    return _upload_to_local(content, ext, employee_id)


def delete_profile_picture(url: str) -> None:
    if not url:
        return
    if settings.STORAGE_BACKEND == "supabase":
        _delete_from_supabase(url)
    else:
        _delete_from_local(url)


# ── Local storage ─────────────────────────────────────────────────────────────

def _upload_to_local(content: bytes, ext: str, employee_id: int) -> str:
    upload_dir = Path(settings.UPLOAD_DIR) / "employees"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Remove any existing file for this employee (handles extension changes)
    for existing in upload_dir.glob(f"{employee_id}.*"):
        existing.unlink()

    filename = f"{employee_id}{ext}"
    (upload_dir / filename).write_bytes(content)

    return f"{settings.API_BASE_URL}/uploads/employees/{filename}"


def _delete_from_local(url: str) -> None:
    prefix = f"{settings.API_BASE_URL}/uploads/"
    if not url.startswith(prefix):
        return
    rel_path = url[len(prefix):]
    file_path = Path(settings.UPLOAD_DIR) / rel_path
    if file_path.exists():
        file_path.unlink()


# ── Supabase storage (direct REST API via httpx — no SDK needed) ──────────────

def _sb_headers(content_type: str | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_KEY,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def _upload_to_supabase(content: bytes, ext: str, employee_id: int) -> str:
    import httpx
    path = f"employees/{employee_id}{ext}"
    base = f"{settings.SUPABASE_URL}/storage/v1"

    httpx.post(
        f"{base}/object/{BUCKET_NAME}/{path}",
        headers={**_sb_headers("image/jpeg"), "x-upsert": "true"},
        content=content,
        timeout=30,
    ).raise_for_status()

    return f"{base}/object/public/{BUCKET_NAME}/{path}"


def _delete_from_supabase(url: str) -> None:
    import httpx
    marker = f"/object/public/{BUCKET_NAME}/"
    if marker not in url:
        return
    path = url.split(marker)[-1]
    httpx.delete(
        f"{settings.SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}",
        headers=_sb_headers("application/json"),
        json={"prefixes": [path]},
        timeout=10,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_extension(filename: str) -> str:
    if filename and "." in filename:
        return Path(filename).suffix.lower()
    return ".jpg"
