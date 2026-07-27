"""Audio format conversion endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transcode import LEGACY_CODECS, PRESETS, Transcoder, ffmpeg_available
from app.db.base import get_session
from app.db.models import Track, TrackStatus
from app.services.job_runner import runner

router = APIRouter()


class ConvertRequest(BaseModel):
    preset: str = Field(default="flac", description="Preset name from GET /convert/presets")
    track_ids: list[int] | None = None
    source_codecs: list[str] | None = Field(
        default=None, description="Convert every track using these codecs, e.g. ['wmav2']")
    legacy_only: bool = True
    keep_original: bool = True
    dest_dir: str | None = Field(
        default=None, description="Write output elsewhere; defaults to alongside the source")
    limit: int = 200
    dry_run: bool = True


@router.get("/presets")
async def presets() -> dict:
    """Available conversion targets, grouped for the UI."""
    return {
        "ffmpeg_available": ffmpeg_available(),
        "lossless": [_preset(p) for p in PRESETS.values() if p.lossless],
        "lossy": [_preset(p) for p in PRESETS.values() if not p.lossless],
    }


@router.get("/candidates")
async def candidates(limit: int = 200, s: AsyncSession = Depends(get_session)) -> dict:
    """Files the engine recommends converting, with the reason and target preset."""
    transcoder = Transcoder()

    rows = (await s.execute(
        select(Track).where(Track.status == TrackStatus.ACTIVE).limit(5000)
    )).scalars().all()

    items = []
    for t in rows:
        suggestion = transcoder.suggest(
            t.codec, t.bitrate, bool(t.lossless), t.size_bytes or 0, t.duration)
        if not suggestion:
            continue
        items.append({
            "track_id": t.id,
            "path": t.path,
            "codec": t.codec,
            "bitrate": t.bitrate,
            "size_bytes": t.size_bytes,
            "duration": t.duration,
            "suggested_preset": suggestion,
            "reason": ("legacy_codec" if (t.codec or "").lower() in LEGACY_CODECS
                       else "oversized_lossless"),
        })
        if len(items) >= limit:
            break

    return {"count": len(items), "items": items}


@router.get("/codecs")
async def codec_breakdown(s: AsyncSession = Depends(get_session)) -> list[dict]:
    """Codec distribution, so the user can convert a whole format at once."""
    rows = (await s.execute(
        select(Track.codec, func.count(Track.id), func.sum(Track.size_bytes),
               func.max(Track.lossless))
        .where(Track.status == TrackStatus.ACTIVE)
        .group_by(Track.codec).order_by(func.count(Track.id).desc())
    )).all()
    return [
        {"codec": codec or "unknown", "count": count, "bytes": total or 0,
         "lossless": bool(lossless), "legacy": (codec or "").lower() in LEGACY_CODECS}
        for codec, count, total, lossless in rows
    ]


@router.post("")
async def convert(req: ConvertRequest = Body(default=ConvertRequest())) -> dict:
    """Start a conversion job. With dry_run=true nothing is written."""
    if req.preset not in PRESETS:
        raise HTTPException(400, f"unknown preset; available: {sorted(PRESETS)}")
    if not ffmpeg_available():
        raise HTTPException(503, "ffmpeg is not available inside the container")
    if req.dest_dir and not Path(req.dest_dir).parent.exists():
        raise HTTPException(400, f"destination parent does not exist: {req.dest_dir}")

    params = req.model_dump(exclude={"dry_run"})
    job_id = await runner.submit("convert", params, dry_run=req.dry_run)
    return {"job_id": job_id, "dry_run": req.dry_run, "preset": req.preset}


def _preset(p) -> dict:
    return {"name": p.name, "ext": p.ext, "lossless": p.lossless,
            "label": p.label, "description": p.description}
