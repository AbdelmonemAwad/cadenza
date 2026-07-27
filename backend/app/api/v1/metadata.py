from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tags import TagSet, read_tags, write_tags
from app.db.base import get_session
from app.db.models import AuditLog, Track
from app.providers.aggregator import MetadataAggregator
from app.services.enrichment import EnrichmentService
from app.services.job_runner import runner

router = APIRouter()


class EnrichRequest(BaseModel):
    track_ids: list[int] | None = None
    only_incomplete: bool = True
    limit: int = 500
    dry_run: bool = True
    overwrite: bool = False
    min_confidence: float = 0.55
    artwork: bool = True
    lyrics: bool = True


class ManualTags(BaseModel):
    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    year: int | None = None
    track_no: int | None = None
    disc_no: int | None = None
    genre: str | None = None
    isrc: str | None = None
    lyrics: str | None = None


class OrganizeRequest(BaseModel):
    template: str | None = None
    path: str | None = None
    track_ids: list[int] | None = None
    dry_run: bool = True


@router.post("/enrich")
async def enrich(req: EnrichRequest = Body(default=EnrichRequest())) -> dict:
    """Run enrichment as a background job. dry_run=true previews without writing."""
    job_id = await runner.submit("enrich", req.model_dump(exclude={"dry_run"}),
                                 dry_run=req.dry_run)
    return {"job_id": job_id, "dry_run": req.dry_run}


@router.get("/lookup/{track_id}")
async def lookup(track_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    """Immediate multi-source preview for a single track."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")

    async with MetadataAggregator(s) as aggregator:
        merged = await aggregator.identify(
            title=t.title, artist=t.artist or t.albumartist, album=t.album,
            duration=t.duration, fingerprint=t.fingerprint, isrc=t.isrc,
            mbid=t.mb_recording_id,
        )
    md = merged.metadata
    return {
        "track_id": track_id,
        "confidence": merged.overall_confidence,
        "proposed": {
            "title": md.title, "artist": md.artist, "albumartist": md.albumartist,
            "album": md.album, "year": md.year, "track_no": md.track_no,
            "total_tracks": md.total_tracks, "disc_no": md.disc_no,
            "genre": md.genre, "isrc": md.isrc,
            "mb_recording_id": md.mb_recording_id, "mb_release_id": md.mb_release_id,
            "apple_id": md.apple_id, "artwork_url": md.artwork_url,
            "has_synced_lyrics": bool(md.synced_lyrics),
        },
        "field_sources": merged.field_sources,
        "field_weights": merged.field_weights,
        "conflicts": merged.conflicts,
        "candidates_by_source": merged.candidates_by_source,
    }


@router.post("/apply/{track_id}")
async def apply_single(track_id: int, dry_run: bool = False, overwrite: bool = False,
                       s: AsyncSession = Depends(get_session)) -> dict:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")
    service = EnrichmentService(s)
    try:
        r = await service.enrich(t, dry_run=dry_run, overwrite=overwrite)
    finally:
        await service.aclose()
    await s.commit()
    return {
        "track_id": r.track_id, "applied": r.applied, "dry_run": r.dry_run,
        "confidence": r.confidence,
        "changed": {k: list(v) for k, v in r.changed_fields.items()},
        "sources": r.field_sources, "conflicts": r.conflicts,
        "artwork": r.artwork, "lyrics": r.lyrics, "error": r.error,
    }


@router.put("/tags/{track_id}")
async def manual_edit(track_id: int, body: ManualTags,
                      s: AsyncSession = Depends(get_session)) -> dict:
    """Direct manual edit: writes to the file and records the change."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")
    path = Path(t.path)
    if not path.is_file():
        raise HTTPException(410, "file no longer exists on disk")

    current: TagSet = read_tags(path)
    changes: dict[str, tuple] = {}
    for name, value in body.model_dump(exclude_none=True).items():
        old = getattr(current, name, None)
        if old != value:
            changes[name] = (old, value)
            setattr(current, name, value)
    if not changes:
        return {"track_id": track_id, "changed": {}}

    try:
        write_tags(path, current)
    except Exception as exc:
        raise HTTPException(500, f"write failed: {exc}") from exc

    for name in ("title", "artist", "albumartist", "album", "year",
                 "track_no", "disc_no", "genre", "isrc"):
        if name in changes:
            setattr(t, name, changes[name][1])
    t.tag_completeness = current.completeness()

    s.add(AuditLog(action="manual_tag_edit", track_id=track_id, src_path=t.path,
                   detail={k: list(v) for k, v in changes.items()}))
    await s.commit()
    return {"track_id": track_id, "changed": {k: list(v) for k, v in changes.items()}}


@router.post("/organize")
async def organize(req: OrganizeRequest = Body(default=OrganizeRequest())) -> dict:
    """Rename and move files according to the configured path template."""
    job_id = await runner.submit("organize", req.model_dump(exclude={"dry_run"}),
                                 dry_run=req.dry_run)
    return {"job_id": job_id, "dry_run": req.dry_run}
