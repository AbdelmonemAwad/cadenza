"""Artwork handling: normalise size and format, embed, and write cover.jpg."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from app.config import get_settings

log = logging.getLogger(__name__)


def normalize(data: bytes, *, target_px: int | None = None,
              quality: int = 92) -> tuple[bytes, str] | None:
    """Convert any cover into a reasonably sized progressive JPEG.

    Without this, a 5000px PNG pulled from a provider would be embedded into
    every track of an album and inflate the library considerably.
    """
    s = get_settings()
    target = target_px or s.artwork_target_px
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        log.debug("artwork decode failed: %s", exc)
        return None

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > target:
        ratio = target / max(w, h)
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue(), "image/jpeg"


def dimensions(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


def save_cover_file(folder: Path, data: bytes, *, overwrite_smaller: bool = True) -> Path | None:
    """Write cover.jpg into an album folder, replacing only lower-resolution art."""
    s = get_settings()
    target = folder / s.cover_filename
    if target.is_file() and overwrite_smaller:
        try:
            old, new = dimensions(target.read_bytes()), dimensions(data)
            if old and new and old[0] >= new[0]:
                return target
        except OSError:
            pass
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".jpg.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        return target
    except OSError as exc:
        log.warning("cover write failed %s: %s", target, exc)
        return None


def cache_path(key: str) -> Path:
    return get_settings().artwork_cache / f"{key}.jpg"


def cache_get(key: str) -> bytes | None:
    p = cache_path(key)
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def cache_put(key: str, data: bytes) -> None:
    p = cache_path(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    except OSError as exc:
        log.debug("artwork cache write failed: %s", exc)
