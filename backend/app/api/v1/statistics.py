"""Usage statistics, and the application's own log.

The dashboard answers "what does my library look like right now". Neither it
nor anything else answered "what has Cadenza actually been doing" — how much
work has run, how much space came back, what failed and when. All of it was
already in the database; nothing read it.

The log was worse: `cadenza.log` is written to the data folder and there was no
way to see it from the interface at all. On a NAS that means SSH, or File
Station and a text editor, to answer "why did that job fail" — so in practice
nobody looked, and the one place the application explains itself went unread.
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_session
from app.db.models import (
    AuditLog,
    DuplicateGroup,
    Job,
    JobState,
    QuarantineItem,
    Track,
    TrackStatus,
)

log = logging.getLogger(__name__)

router = APIRouter()

# Long enough to be useful, short enough that the aggregates stay cheap on
# SQLite: `ts` and `created_at` are indexed, the rest of these columns are not.
MAX_DAYS = 365
DEFAULT_DAYS = 30

# How much of the log to hand back at once. The file rotates at 8 MB, and a
# browser rendering all of it is no more readable than the file itself.
MAX_LOG_LINES = 2000
DEFAULT_LOG_LINES = 300

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _since(days: int) -> datetime:
    # Naive UTC, because that is what the models store: `_utcnow()` is aware
    # but job_runner and quarantine both write `.replace(tzinfo=None)`, and
    # comparing an aware value against those raises.
    return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)


@router.get("")
async def statistics(days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
                     s: AsyncSession = Depends(get_session)) -> dict:
    """What Cadenza has been doing, over the last `days` days."""
    since = _since(days)
    active = Track.status == TrackStatus.ACTIVE

    async def scalar(stmt, default=0):
        return (await s.execute(stmt)).scalar() or default

    # ---- The library as it stands ----
    library = {
        "tracks": await scalar(select(func.count(Track.id)).where(active)),
        "bytes": await scalar(select(func.sum(Track.size_bytes)).where(active)),
        "seconds": await scalar(select(func.sum(Track.duration)).where(active)),
        "artists": await scalar(
            select(func.count(func.distinct(Track.albumartist))).where(active)),
        "albums": await scalar(
            select(func.count(func.distinct(Track.album))).where(active)),
        "lossless": await scalar(select(func.count(Track.id)).where(
            active, Track.lossless == True)),                       # noqa: E712
        "with_artwork": await scalar(select(func.count(Track.id)).where(
            active, Track.has_artwork == True)),                    # noqa: E712
        "with_lyrics": await scalar(select(func.count(Track.id)).where(
            active, Track.has_lyrics == True)),                     # noqa: E712
    }

    # ---- What has been done ----
    jobs_by_kind = [
        {"kind": kind, "state": state, "count": count}
        for kind, state, count in (await s.execute(
            select(Job.kind, Job.state, func.count(Job.id))
            .where(Job.created_at >= since)
            .group_by(Job.kind, Job.state)
            .order_by(func.count(Job.id).desc())
        )).all()
    ]

    actions = [
        {"action": action, "count": count}
        for action, count in (await s.execute(
            select(AuditLog.action, func.count(AuditLog.id))
            .where(AuditLog.ts >= since)
            .group_by(AuditLog.action)
            .order_by(func.count(AuditLog.id).desc())
            .limit(20)
        )).all()
    ]

    # ---- Activity per day ----
    #
    # `date(ts)` rather than a Python-side bucket: pulling every row back to
    # group it here would mean loading the whole audit log to draw a chart.
    # SQLite's date() on a naive UTC string is the same day the row was
    # written, which is what the chart is about.
    per_day_rows = (await s.execute(
        select(func.date(AuditLog.ts), func.count(AuditLog.id))
        .where(AuditLog.ts >= since)
        .group_by(func.date(AuditLog.ts))
        .order_by(func.date(AuditLog.ts))
    )).all()
    # Days with no activity are filled in, so a chart does not silently
    # compress a quiet week into nothing.
    counts = dict(per_day_rows)
    per_day = []
    day = since.date()
    today = datetime.now(UTC).date()
    while day <= today:
        key = day.isoformat()
        per_day.append({"day": key, "count": int(counts.get(key, 0))})
        day += timedelta(days=1)

    # ---- Space ----
    quarantined_bytes = await scalar(
        select(func.sum(QuarantineItem.size_bytes)).where(
            QuarantineItem.restored == False))                      # noqa: E712
    reclaimable = await scalar(
        select(func.sum(DuplicateGroup.reclaimable_bytes)).where(
            DuplicateGroup.resolved == False))                      # noqa: E712

    return {
        "window_days": days,
        "since": since.isoformat(),
        "library": library,
        "jobs": jobs_by_kind,
        "actions": actions,
        "per_day": per_day,
        "space": {
            "library_bytes": library["bytes"],
            "in_quarantine_bytes": quarantined_bytes,
            "reclaimable_bytes": reclaimable,
        },
        "totals": {
            "jobs": sum(j["count"] for j in jobs_by_kind),
            "failed_jobs": sum(j["count"] for j in jobs_by_kind
                               if j["state"] == JobState.FAILED.value),
            "actions": sum(a["count"] for a in actions),
        },
    }


@router.get("/log")
async def application_log(
    lines: int = Query(DEFAULT_LOG_LINES, ge=1, le=MAX_LOG_LINES),
    level: str | None = Query(None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    contains: str | None = Query(None, max_length=200),
) -> dict:
    """The tail of `cadenza.log`.

    No path parameter, deliberately. The only file this can ever read is the
    one the application is writing, resolved from settings — a log viewer that
    takes a filename is an arbitrary file read wearing a hat, and this endpoint
    would be an attractive one because the log is expected to contain paths.

    The contents are already redacted on the way in: `RedactingFormatter`
    replaces the AcoustID, Last.fm and Discogs credentials before a line is
    written, which matters because two of those APIs require the key in the
    query string and any httpx error quotes the full URL.
    """
    path = Path(get_settings().config_dir) / "logs" / "cadenza.log"
    if not path.is_file():
        return {"path": str(path), "exists": False, "lines": [],
                "note": "no log file yet"}

    try:
        tail = _tail(path, lines * 4 if (level or contains) else lines)
    except OSError as exc:
        log.warning("could not read the application log", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "could not read the log file") from exc

    if level:
        tail = [line for line in tail if f" {level:<7} " in line or f" {level} " in line]
    if contains:
        needle = contains.lower()
        tail = [line for line in tail if needle in line.lower()]

    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "levels": list(LOG_LEVELS),
        "lines": tail[-lines:],
    }


def _tail(path: Path, count: int, block: int = 64 * 1024) -> list[str]:
    """The last `count` lines, without reading the whole file.

    The log rotates at 8 MB and there can be five rotations behind it; reading
    it all to show the last screenful would be pointless on a NAS with 2 GB of
    RAM shared with everything else.
    """
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        data = b""
        while end > 0 and data.count(b"\n") <= count:
            step = min(block, end)
            end -= step
            handle.seek(end)
            data = handle.read(step) + data

    text = data.decode("utf-8", "replace")
    return [line for line in text.splitlines() if line.strip()][-count:]


@router.get("/summary")
async def summary(s: AsyncSession = Depends(get_session)) -> dict:
    """A compact set of headline figures, for a card rather than a page."""
    since = _since(7)

    async def scalar(stmt, default=0):
        return (await s.execute(stmt)).scalar() or default

    recent_jobs = OrderedDict()
    for kind, count in (await s.execute(
        select(Job.kind, func.count(Job.id))
        .where(Job.created_at >= since).group_by(Job.kind)
    )).all():
        recent_jobs[kind] = count

    return {
        "tracks": await scalar(select(func.count(Track.id)).where(
            Track.status == TrackStatus.ACTIVE)),
        "jobs_last_7_days": dict(recent_jobs),
        "actions_last_7_days": await scalar(
            select(func.count(AuditLog.id)).where(AuditLog.ts >= since)),
    }
