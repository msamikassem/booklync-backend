"""Regression tests for site-asset image compression."""

import io

from PIL import Image

from app.services import storage_service


def test_compress_downscales_and_converts_to_webp():
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(buf, format="PNG")
    out, ext = storage_service._compress_site_image(buf.getvalue(), ".png")

    assert ext == ".webp"
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert max(img.size) <= storage_service._SITE_IMAGE_MAX_DIM


def test_compress_preserves_alpha():
    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), (10, 20, 30, 128)).save(buf, format="PNG")
    out, ext = storage_service._compress_site_image(buf.getvalue(), ".png")
    assert ext == ".webp"
    assert Image.open(io.BytesIO(out)).mode in ("RGBA", "LA", "P")


def test_compress_passes_through_svg():
    raw = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    out, ext = storage_service._compress_site_image(raw, ".svg")
    assert ext == ".svg"
    assert out == raw
