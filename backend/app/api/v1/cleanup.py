"""Tidying endpoints: see what can go before anything goes.

Two endpoints, and the split matters. `GET /cleanup/scan` writes nothing ever —
it is how the user finds out what "tidy up" would mean for their library, and
it can be called as often as they like. `POST /cleanup` submits a job, and
defaults to a preview even then.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cleanup import CATEGORIES, LibraryCleaner
from app.db.base import get_session
from app.services.job_runner import runner

router = APIRouter()

# What each category means, in the user's terms rather than the code's. The
# interface reads this instead of hardcoding a list that would drift.
DESCRIPTIONS: dict[str, dict[str, str]] = {
    "empty_folders": {
        "what": "Folders with nothing in them",
        "detail": "Left behind when an album was moved or its files removed. "
                  "Deleted outright — an empty folder holds nothing to lose, "
                  "and it reappears the moment anything is written there.",
    },
    "orphan_sidecars": {
        "what": "Lyrics and cover files with no music beside them",
        "detail": "A .lrc, .cue, .log, playlist or cover.jpg in a folder that "
                  "holds no audio at all. Moved to quarantine, not deleted — "
                  "it may be lyrics you wrote yourself.",
    },
    "nas_metadata": {
        "what": "Synology's own caches inside your library",
        "detail": "@eaDir, #recycle, @tmp and #snapshot. DSM regenerates "
                  "these; nothing of yours lives in them. Refused if one "
                  "somehow contains audio.",
    },
    "leftover_temp": {
        "what": "Half-written files from an interrupted job",
        "detail": "Files still carrying a .part-, .tmp or .partial marker, "
                  "left by a conversion or move that did not finish.",
    },
    "missing_tracks": {
        "what": "Index entries for files that are gone",
        "detail": "Tracks marked missing whose file is no longer on disk. "
                  "Removes the database row only; there is no file to touch.",
    },
    "stale_quarantine": {
        "what": "Quarantine records whose file has vanished",
        "detail": "Rows that still count toward your quarantine totals but "
                  "can never be restored, because the file they point at is "
                  "no longer there. Removes the row only.",
    },
}


class CleanupRequest(BaseModel):
    categories: list[str] = Field(default_factory=list)
    # Preview by default, like every other destructive operation here. The
    # caller has to ask for the real thing.
    dry_run: bool = True
    limit: int = Field(default=5000, ge=1, le=50_000)


@router.get("/categories")
async def categories() -> dict:
    """What can be tidied, and what each one means."""
    return {
        "categories": [
            {"key": key, **DESCRIPTIONS[key]} for key in CATEGORIES
        ],
        "note": "Nothing here ever removes an audio file. Anything that is not "
                "obviously worthless goes to quarantine, where you can put it "
                "back.",
    }


@router.get("/scan")
async def scan(category: list[str] = Query(default=[]),
               limit: int = Query(default=5000, ge=1, le=50_000),
               s: AsyncSession = Depends(get_session)) -> dict:
    """What would be removed. Writes nothing, ever."""
    wanted = set(category) or set(CATEGORIES)
    try:
        report = await LibraryCleaner(s).scan(wanted, limit=limit)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "categories": sorted(wanted),
        "found": len(report.items),
        "by_category": report.by_category(),
        "refused": report.refused[:50],
        "items": [item.as_dict() for item in report.items][:500],
    }


@router.post("")
async def clean(req: CleanupRequest = Body(default=CleanupRequest())) -> dict:
    """Start a tidy-up. With dry_run=true — the default — nothing is written."""
    unknown = set(req.categories) - set(CATEGORIES)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"unknown categories: {sorted(unknown)}")
    if not req.categories:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "choose at least one thing to tidy. Nothing is selected by default, "
            "because the categories do very different things.")

    job_id = await runner.submit(
        "cleanup",
        {"categories": sorted(req.categories), "limit": req.limit},
        dry_run=req.dry_run)
    return {"job_id": job_id, "dry_run": req.dry_run,
            "categories": sorted(req.categories)}
