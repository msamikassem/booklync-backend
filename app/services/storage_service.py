"""
app/services/storage_service.py

Handles file storage for profile pictures.
Supports two backends controlled by STORAGE_BACKEND in .env:
  - "local"    → saves to UPLOAD_DIR on disk, served via /uploads
  - "supabase" → uploads to Supabase Storage (bucket: profile-pictures)
"""

import os
import uuid
from pathlib import Path
from fastapi import HTTPException

from app.core.config import settings

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
BUCKET_NAME = "profile-pictures"

# Website-builder assets (logos, hero/gallery images) live in their own bucket /
# subdirectory and allow a larger size than profile pictures.
SITE_BUCKET_NAME = "site-assets"


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


# ── Website-builder assets ─────────────────────────────────────────────────────
# Same STORAGE_BACKEND switch as profile pictures, so moving to Supabase later is
# a config change only. Files are namespaced per branch and given a random name
# (multiple images per site, unlike the one-per-employee profile picture).

def upload_site_asset(content: bytes, filename: str, agent_id: int) -> str:
    max_bytes = settings.SITE_ASSET_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {settings.SITE_ASSET_MAX_MB}MB.",
        )

    # Downscale + recompress so published sites load fast (owners often upload
    # multi-MB phone photos). Vector/icon files pass through untouched.
    content, ext = _compress_site_image(content, _get_extension(filename))
    name = f"{uuid.uuid4().hex}{ext}"

    if settings.STORAGE_BACKEND == "supabase":
        return _upload_site_asset_to_supabase(content, agent_id, name)
    return _upload_site_asset_to_local(content, agent_id, name)


# Raster formats we recompress. SVG/ICO (vector/icon) and animated GIF are left
# as-is so we don't rasterize vectors or flatten animations.
_RASTER_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_SITE_IMAGE_MAX_DIM = 2048


def _compress_site_image(content: bytes, ext: str) -> tuple[bytes, str]:
    """Downscale to max dimension + re-encode as WebP. Returns (bytes, ext).
    Best-effort: any failure returns the original bytes untouched."""
    if ext not in _RASTER_EXT:
        return content, ext
    try:
        import io
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)  # honour camera rotation, then drop EXIF
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((_SITE_IMAGE_MAX_DIM, _SITE_IMAGE_MAX_DIM))  # only shrinks; keeps aspect

        out = io.BytesIO()
        img.save(out, format="WEBP", quality=82, method=6)
        return out.getvalue(), ".webp"
    except Exception:
        return content, ext


def delete_site_asset(url: str) -> None:
    if not url:
        return
    if settings.STORAGE_BACKEND == "supabase":
        _delete_site_asset_from_supabase(url)
    else:
        _delete_from_local(url)  # local layout is shared (URL → path under UPLOAD_DIR)


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


def _upload_site_asset_to_local(content: bytes, agent_id: int, name: str) -> str:
    upload_dir = Path(settings.UPLOAD_DIR) / "sites" / str(agent_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / name).write_bytes(content)
    return f"{settings.API_BASE_URL}/uploads/sites/{agent_id}/{name}"


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


def _upload_site_asset_to_supabase(content: bytes, agent_id: int, name: str) -> str:
    import httpx
    path = f"{agent_id}/{name}"
    base = f"{settings.SUPABASE_URL}/storage/v1"
    httpx.post(
        f"{base}/object/{SITE_BUCKET_NAME}/{path}",
        headers={**_sb_headers(_content_type_for(name)), "x-upsert": "true"},
        content=content,
        timeout=30,
    ).raise_for_status()
    return f"{base}/object/public/{SITE_BUCKET_NAME}/{path}"


def _delete_site_asset_from_supabase(url: str) -> None:
    import httpx
    marker = f"/object/public/{SITE_BUCKET_NAME}/"
    if marker not in url:
        return
    path = url.split(marker)[-1]
    httpx.delete(
        f"{settings.SUPABASE_URL}/storage/v1/object/{SITE_BUCKET_NAME}",
        headers=_sb_headers("application/json"),
        json={"prefixes": [path]},
        timeout=10,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_extension(filename: str) -> str:
    if filename and "." in filename:
        return Path(filename).suffix.lower()
    return ".jpg"


_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _content_type_for(name: str) -> str:
    return _CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
