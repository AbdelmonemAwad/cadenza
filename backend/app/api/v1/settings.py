from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel

from app.config import APP_NAME, APP_VERSION, get_settings, save_settings
from app.core.settings_policy import (
    LOCKED_FIELDS,
    WRITABLE_FIELDS,
    SettingRejected,
    validate_patch,
)
from app.core.transcode import ffmpeg_available

router = APIRouter()

# Never echoed back to the UI; reported as configured/not configured only.
SECRET_FIELDS = {"acoustid_api_key", "discogs_token", "lastfm_api_key"}


class SettingsPatch(BaseModel):
    model_config = {"extra": "allow"}


@router.get("")
async def read() -> dict:
    s = get_settings()
    data: dict[str, Any] = {}
    for name in s.model_fields:
        value = getattr(s, name)
        if name in SECRET_FIELDS:
            data[name] = bool(value)          # boolean only, never the secret
        elif isinstance(value, Path):
            data[name] = str(value)
        else:
            data[name] = value
    return data


@router.get("/writable")
async def writable_fields() -> dict:
    """Which settings the API will accept, so the UI need not guess."""
    return {
        "writable": sorted(WRITABLE_FIELDS),
        "locked": sorted(LOCKED_FIELDS),
        "note": "Locked settings are deployment configuration: set them in the "
                "compose file or the environment, not over the network.",
    }


@router.patch("")
async def update(patch: SettingsPatch = Body(...)) -> dict:
    """Apply a settings patch.

    Values are validated, not just field names. Accepting any known field
    unchecked made this endpoint an arbitrary-write primitive: it could
    repoint the binaries the app executes, turn the artwork endpoint into an
    arbitrary file read, or set a quarantine retention of zero. See issue #6.
    """
    try:
        cleaned = validate_patch(patch.model_dump())
    except SettingRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    s = save_settings(cleaned)
    return {"updated": sorted(cleaned), "dry_run_default": s.dry_run_default}


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    fpcalc_ok = bool(shutil.which(s.fpcalc_bin))
    ffmpeg_ok = ffmpeg_available()

    def dir_state(p: Path) -> dict:
        return {"path": str(p), "exists": p.exists(),
                "writable": p.exists() and _writable(p)}

    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "tools": {
            "ffmpeg": ffmpeg_ok, "fpcalc": fpcalc_ok,
            "note": None if (ffmpeg_ok and fpcalc_ok)
            else "Some tools are missing; fingerprinting or conversion may not work",
        },
        "paths": {
            "music_root": dir_state(s.music_root),
            "quarantine": dir_state(s.quarantine_root),
            "config": dir_state(s.config_dir),
        },
        "providers": {
            "acoustid": bool(s.acoustid_api_key),
            "musicbrainz": True,
            "discogs": bool(s.discogs_token),
            "lastfm": bool(s.lastfm_api_key),
            "applemusic": bool(s.apple_team_id and s.apple_key_id
                               and s.apple_key_file.is_file()),
            "lrclib": True,
        },
        "safety": {
            "dry_run_default": s.dry_run_default,
            "hard_delete_allowed": s.hard_delete_allowed,
            "quarantine_retention_days": s.quarantine_retention_days,
        },
    }


def _writable(p: Path) -> bool:
    probe = p / ".cadenza_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
