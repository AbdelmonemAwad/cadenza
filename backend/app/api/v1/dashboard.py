from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import (
    AuditLog,
    DuplicateGroup,
    Job,
    QuarantineItem,
    Track,
    TrackStatus,
)

router = APIRouter()


@router.get("/stats")
async def stats(s: AsyncSession = Depends(get_session)) -> dict:
    async def scalar(stmt, default=0):
        return (await s.execute(stmt)).scalar() or default

    active = Track.status == TrackStatus.ACTIVE

    total = await scalar(select(func.count(Track.id)).where(active))
    bytes_total = await scalar(select(func.sum(Track.size_bytes)).where(active))
    lossless = await scalar(select(func.count(Track.id)).where(
        active, Track.lossless == True))  # noqa: E712
    corrupt = await scalar(select(func.count(Track.id)).where(
        Track.status == TrackStatus.CORRUPT))
    missing = await scalar(select(func.count(Track.id)).where(
        Track.status == TrackStatus.MISSING))
    no_artwork = await scalar(select(func.count(Track.id)).where(
        active, Track.has_artwork == False))  # noqa: E712
    no_lyrics = await scalar(select(func.count(Track.id)).where(
        active, Track.has_lyrics == False))  # noqa: E712
    incomplete = await scalar(select(func.count(Track.id)).where(
        active, Track.tag_completeness < 0.85))
    avg_quality = await scalar(select(func.avg(Track.quality_score)).where(active), 0.0)

    unresolved = DuplicateGroup.resolved == False  # noqa: E712
    dup_groups = await scalar(select(func.count(DuplicateGroup.id)).where(unresolved))
    dup_files = await scalar(
        select(func.sum(DuplicateGroup.member_count - 1)).where(unresolved))
    reclaimable = await scalar(
        select(func.sum(DuplicateGroup.reclaimable_bytes)).where(unresolved))

    in_quarantine = QuarantineItem.restored == False  # noqa: E712
    q_items = await scalar(select(func.count(QuarantineItem.id)).where(in_quarantine))
    q_bytes = await scalar(select(func.sum(QuarantineItem.size_bytes)).where(in_quarantine))

    by_codec = [
        {"codec": c or "unknown", "count": n, "bytes": b or 0}
        for c, n, b in (await s.execute(
            select(Track.codec, func.count(Track.id), func.sum(Track.size_bytes))
            .where(active).group_by(Track.codec).order_by(func.count(Track.id).desc())
        )).all()
    ]

    # An album is incomplete when we hold fewer tracks than its declared total.
    #
    # `have` must count every track of the album; only `expected` needs a
    # total_tracks tag. The same WHERE used to restrict both, so an album where
    # eight of twelve files carried the tag was counted as having eight of
    # twelve and reported as missing four -- when in fact all twelve were
    # there. Ripping software routinely writes the total on some tracks and not
    # others, so this misreported complete albums as incomplete for a large
    # part of a typical library.
    #
    # COUNT over a CASE rather than a second query: the totals have to come
    # from the same grouping to line up.
    incomplete_albums = (await s.execute(
        select(Track.albumartist, Track.album,
               func.count(Track.id).label("have"),
               func.max(Track.total_tracks).label("expected"))
        .where(active, Track.album.isnot(None))
        .group_by(Track.albumartist, Track.album)
        .having(func.max(Track.total_tracks).isnot(None))
        .having(func.count(Track.id) < func.max(Track.total_tracks))
        .order_by((func.max(Track.total_tracks) - func.count(Track.id)).desc())
        .limit(50)
    )).all()

    return {
        "library": {
            "tracks": total,
            "bytes": bytes_total,
            "lossless": lossless,
            "lossy": max(0, total - lossless),
            "corrupt": corrupt,
            "missing": missing,
            "avg_quality": round(float(avg_quality or 0), 4),
            "by_codec": by_codec,
        },
        "quality_gaps": {
            "missing_artwork": no_artwork,
            "missing_lyrics": no_lyrics,
            "incomplete_tags": incomplete,
            "incomplete_albums": [
                {"albumartist": a, "album": al, "have": have, "expected": expected,
                 "missing": (expected or 0) - have}
                for a, al, have, expected in incomplete_albums
            ],
        },
        "duplicates": {
            "groups": dup_groups,
            "files": dup_files,
            "reclaimable_bytes": reclaimable,
        },
        "quarantine": {"items": q_items, "bytes": q_bytes},
    }


@router.get("/recent-jobs")
async def recent_jobs(limit: int = 10, s: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await s.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit))).scalars().all()
    return [
        {"id": j.id, "kind": j.kind,
         "state": j.state if isinstance(j.state, str) else j.state.value,
         "dry_run": j.dry_run, "processed": j.processed, "total": j.total,
         "message": j.message, "created_at": j.created_at, "finished_at": j.finished_at}
        for j in rows
    ]


@router.get("/audit")
async def audit(limit: int = 100, offset: int = 0, action: str | None = None,
                level: str | None = None,
                s: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(AuditLog).order_by(AuditLog.ts.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if level:
        stmt = stmt.where(AuditLog.level == level)
    total = (await s.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await s.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {
        "total": total,
        "items": [
            {"id": r.id, "ts": r.ts, "action": r.action, "level": r.level,
             "track_id": r.track_id, "job_id": r.job_id, "src": r.src_path,
             "dst": r.dst_path, "detail": r.detail, "reversible": r.reversible}
            for r in rows
        ],
    }
