from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.tags import extract_artwork, read_tags
from app.db.base import get_session
from app.db.models import Track, TrackStatus

router = APIRouter()


def _serialize(t: Track) -> dict:
    return {
        "id": t.id, "path": t.path, "filename": t.filename, "ext": t.ext,
        "size_bytes": t.size_bytes, "codec": t.codec, "lossless": t.lossless,
        "bitrate": t.bitrate, "sample_rate": t.sample_rate, "bit_depth": t.bit_depth,
        "duration": t.duration, "title": t.title, "artist": t.artist,
        "albumartist": t.albumartist, "album": t.album, "year": t.year,
        "track_no": t.track_no, "disc_no": t.disc_no, "genre": t.genre,
        "isrc": t.isrc, "has_artwork": t.has_artwork, "artwork_px": t.artwork_px,
        "has_lyrics": t.has_lyrics, "has_synced_lyrics": t.has_synced_lyrics,
        "tag_completeness": t.tag_completeness, "quality_score": t.quality_score,
        "status": t.status if isinstance(t.status, str) else t.status.value,
        "error": t.error, "mb_recording_id": t.mb_recording_id, "apple_id": t.apple_id,
    }


@router.get("/tracks")
async def list_tracks(
    q: str | None = None,
    status: str | None = None,
    codec: str | None = None,
    lossless: bool | None = None,
    missing_artwork: bool | None = None,
    incomplete_tags: bool | None = None,
    sort: str = Query("path", pattern="^(path|title|artist|album|size_bytes|"
                                       "quality_score|tag_completeness|duration|year)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
    s: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Track)

    if q:
        # FTS5 is far faster than LIKE on a large library; fall back if the
        # virtual table is unavailable or the query is not valid FTS syntax.
        try:
            ids = [r[0] for r in (await s.execute(
                text("SELECT rowid FROM track_fts WHERE track_fts MATCH :q LIMIT 5000"),
                {"q": f"{q}*"},
            )).all()]
            stmt = stmt.where(Track.id.in_(ids)) if ids else stmt.where(text("0"))
        except Exception:
            like = f"%{q}%"
            stmt = stmt.where(or_(Track.title.like(like), Track.artist.like(like),
                                  Track.album.like(like), Track.path.like(like)))

    if status:
        stmt = stmt.where(Track.status == status)
    if codec:
        stmt = stmt.where(Track.codec == codec)
    if lossless is not None:
        stmt = stmt.where(Track.lossless == lossless)
    if missing_artwork:
        stmt = stmt.where(Track.has_artwork == False)  # noqa: E712
    if incomplete_tags:
        stmt = stmt.where(Track.tag_completeness < 0.85)

    total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0

    column = getattr(Track, sort)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    rows = (await s.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return {"total": total, "limit": limit, "offset": offset,
            "items": [_serialize(t) for t in rows]}


@router.get("/tracks/{track_id}")
async def get_track(track_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")
    data = _serialize(t)
    path = Path(t.path)
    if path.is_file():
        data["tags_on_disk"] = read_tags(path).to_dict()
    return data


@router.get("/tracks/{track_id}/artwork")
async def track_artwork(track_id: int, s: AsyncSession = Depends(get_session)) -> Response:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")
    path = Path(t.path)
    art = extract_artwork(path) if path.is_file() else None
    cache = {"Cache-Control": "public, max-age=86400"}
    if art is None:
        cover = path.parent / get_settings().cover_filename
        if cover.is_file():
            return Response(cover.read_bytes(), media_type="image/jpeg", headers=cache)
        raise HTTPException(404, "no artwork available")
    return Response(art[0], media_type=art[1], headers=cache)


@router.get("/tracks/{track_id}/lyrics")
async def track_lyrics(track_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "track not found")
    path = Path(t.path)
    lrc = path.with_suffix(".lrc")
    tags = read_tags(path) if path.is_file() else None
    return {
        "synced": lrc.read_text("utf-8", errors="ignore") if lrc.is_file()
        else (tags.synced_lyrics if tags else None),
        "plain": tags.lyrics if tags else None,
    }


@router.get("/albums")
async def albums(incomplete_only: bool = False, limit: int = 200, offset: int = 0,
                 s: AsyncSession = Depends(get_session)) -> dict:
    stmt = (
        select(
            Track.albumartist, Track.album, Track.year,
            func.count(Track.id).label("tracks"),
            func.max(Track.total_tracks).label("expected"),
            func.sum(Track.size_bytes).label("bytes"),
            func.avg(Track.quality_score).label("quality"),
            func.min(Track.id).label("sample_id"),
        )
        .where(Track.status == TrackStatus.ACTIVE, Track.album.isnot(None))
        .group_by(Track.albumartist, Track.album)
    )
    if incomplete_only:
        stmt = stmt.having(func.count(Track.id) < func.max(Track.total_tracks))
    stmt = stmt.order_by(Track.albumartist, Track.album).offset(offset).limit(limit)

    rows = (await s.execute(stmt)).all()
    return {
        "items": [
            {"albumartist": a, "album": al, "year": y, "tracks": n,
             "expected": expected, "missing": max(0, (expected or n) - n),
             "bytes": b or 0, "quality": round(float(quality or 0), 4),
             "sample_track_id": sample_id}
            for a, al, y, n, expected, b, quality, sample_id in rows
        ]
    }


@router.get("/codecs")
async def codecs(s: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await s.execute(
        select(Track.codec, func.count(Track.id), func.sum(Track.size_bytes))
        .where(Track.status == TrackStatus.ACTIVE)
        .group_by(Track.codec).order_by(func.count(Track.id).desc())
    )).all()
    return [{"codec": c or "unknown", "count": n, "bytes": b or 0} for c, n, b in rows]
