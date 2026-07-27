from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.config import APP_NAME, APP_VERSION, get_settings, save_settings
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


@router.patch("")
async def update(patch: SettingsPatch = Body(...)) -> dict:
    data = patch.model_dump()
    unknown = set(data) - set(get_settings().model_fields)
    if unknown:
        raise HTTPException(400, f"unknown settings fields: {sorted(unknown)}")
    s = save_settings(data)
    return {"updated": sorted(data), "dry_run_default": s.dry_run_default}


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
                               and Path(s.apple_private_key_path).is_file()),
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
