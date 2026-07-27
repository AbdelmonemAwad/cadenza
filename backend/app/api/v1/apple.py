"""Apple Music integration: library matching, playlists and missing-track discovery."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.dedup import normalize_text
from app.core.secretfile import write_private_text
from app.db.base import get_session
from app.db.models import Playlist, Track, TrackStatus
from app.providers.applemusic import TOKEN_TTL, AppleMusicProvider
from app.providers.base import ProviderError

router = APIRouter()

USER_TOKEN_FILE = "apple_user_token.json"
MATCH_THRESHOLD = 85     # rapidfuzz score required to bind a local track to a catalogue song


class MatchRequest(BaseModel):
    track_ids: list[int] | None = None
    limit: int = 200
    min_confidence: float = 0.7
    write_apple_id: bool = False


class ExportRequest(BaseModel):
    name: str
    description: str = "Created by Cadenza"
    track_ids: list[int]


def _load_user_token() -> str | None:
    path = get_settings().config_dir / USER_TOKEN_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text("utf-8")).get("token")
    except (OSError, json.JSONDecodeError):
        return None


def _provider(s: AsyncSession, user_token: str | None = None) -> AppleMusicProvider:
    provider = AppleMusicProvider(s, user_token=user_token or _load_user_token())
    if not provider.enabled:
        raise HTTPException(
            400, "Apple Music is not configured: set TEAM_ID, KEY_ID and the .p8 key")
    return provider


@router.get("/status")
async def status(s: AsyncSession = Depends(get_session)) -> dict:
    cfg = get_settings()
    configured = bool(cfg.apple_team_id and cfg.apple_key_id
                      and Path(cfg.apple_private_key_path).is_file())
    matched = (await s.execute(
        select(func.count(Track.id)).where(Track.apple_id.isnot(None)))).scalar() or 0
    return {"configured": configured, "user_linked": bool(_load_user_token()),
            "storefront": cfg.apple_storefront, "matched_tracks": matched}


@router.get("/developer-token")
async def developer_token(response: Response,
                          s: AsyncSession = Depends(get_session)) -> dict:
    """Consumed by MusicKit JS in the browser to start the user sign-in flow.

    Behind the session dependency like every other route here, and short-lived:
    the token is signed with the user's Apple Developer private key, identifies
    their team, and cannot be revoked on its own. `expires_in` tells the UI when
    to ask again.
    """
    try:
        provider = _provider(s)
        token = provider.developer_token()
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    # A credential must not sit in a shared cache or a browser's disk cache.
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "expires_in": TOKEN_TTL}


@router.post("/link")
async def link(music_user_token: str = Body(..., embed=True)) -> dict:
    """Store the Music-User-Token returned by MusicKit JS after the user consents."""
    # Written 0600 from the moment the file exists. Creating it and then
    # chmod-ing left the token readable at the process umask in between, on a
    # config volume that is a DSM shared folder.
    write_private_text(get_settings().config_dir / USER_TOKEN_FILE, json.dumps({
        "token": music_user_token,
        "saved_at": datetime.now(UTC).isoformat(),
    }))
    return {"linked": True}


@router.delete("/link")
async def unlink() -> dict:
    (get_settings().config_dir / USER_TOKEN_FILE).unlink(missing_ok=True)
    return {"linked": False}


@router.post("/match")
async def match_library(req: MatchRequest = Body(default=MatchRequest()),
                        s: AsyncSession = Depends(get_session)) -> dict:
    """Match local tracks against the Apple catalogue (ISRC first, then text)."""
    provider = _provider(s)
    stmt = select(Track).where(Track.status == TrackStatus.ACTIVE)
    if req.track_ids:
        stmt = stmt.where(Track.id.in_(req.track_ids))
    else:
        stmt = stmt.where(Track.apple_id.is_(None))
    tracks = list((await s.execute(stmt.limit(req.limit))).scalars().all())

    matched, unmatched = [], []
    try:
        for t in tracks:
            try:
                candidates = await provider.lookup(
                    title=t.title, artist=t.artist or t.albumartist, album=t.album,
                    duration=t.duration, isrc=t.isrc)
            except ProviderError as exc:
                unmatched.append({"track_id": t.id, "path": t.path, "error": str(exc)})
                continue

            best = candidates[0] if candidates else None
            if best and best.confidence >= req.min_confidence:
                if req.write_apple_id:
                    t.apple_id = best.apple_id
                matched.append({
                    "track_id": t.id, "title": t.title, "artist": t.artist,
                    "apple_id": best.apple_id, "apple_title": best.title,
                    "apple_artist": best.artist, "apple_album": best.album,
                    "confidence": best.confidence, "url": (best.extra or {}).get("url"),
                    "artwork_url": best.artwork_url,
                })
            else:
                unmatched.append({"track_id": t.id, "path": t.path,
                                  "best_confidence": best.confidence if best else 0.0})
    finally:
        await provider.aclose()

    await s.commit()
    return {"checked": len(tracks), "matched": len(matched),
            "unmatched": len(unmatched), "items": matched[:300],
            "unmatched_items": unmatched[:100]}


@router.get("/albums/{album_id}/missing")
async def missing_from_album(album_id: str, albumartist: str, album: str,
                             s: AsyncSession = Depends(get_session)) -> dict:
    """Compare an Apple album against the local library and link the gaps."""
    provider = _provider(s)
    try:
        catalog = await provider.album_tracks(album_id)
    finally:
        await provider.aclose()

    local = {
        (row[0] or "").strip().lower()
        for row in (await s.execute(
            select(Track.title).where(Track.albumartist == albumartist,
                                      Track.album == album)
        )).all()
    }
    missing = []
    for item in catalog:
        attrs = item.get("attributes") or {}
        name = (attrs.get("name") or "").strip()
        if name and name.lower() not in local:
            missing.append({
                "track_number": attrs.get("trackNumber"),
                "title": name,
                "duration_ms": attrs.get("durationInMillis"),
                "apple_id": item.get("id"),
                "url": attrs.get("url"),
            })
    return {"album": album, "albumartist": albumartist,
            "catalog_tracks": len(catalog), "local_tracks": len(local),
            "missing": missing}


@router.get("/playlists")
async def playlists(s: AsyncSession = Depends(get_session)) -> dict:
    provider = _provider(s)
    try:
        items = await provider.list_playlists()
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await provider.aclose()
    return {
        "items": [
            {"id": p.get("id"),
             "name": (p.get("attributes") or {}).get("name"),
             "description": (((p.get("attributes") or {}).get("description") or {})
                             .get("standard")),
             "can_edit": (p.get("attributes") or {}).get("canEdit")}
            for p in items
        ]
    }


@router.post("/playlists/{playlist_id}/import")
async def import_playlist(playlist_id: str,
                          s: AsyncSession = Depends(get_session)) -> dict:
    """Import an Apple playlist and bind its entries to local tracks."""
    provider = _provider(s)
    try:
        items = await provider.playlist_tracks(playlist_id)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await provider.aclose()

    local = list((await s.execute(
        select(Track).where(Track.status == TrackStatus.ACTIVE))).scalars().all())
    index = [(t, normalize_text(t.title), normalize_text(t.artist or t.albumartist))
             for t in local]

    matched_ids, unmatched = [], []
    for item in items:
        attrs = item.get("attributes") or {}
        want_title = normalize_text(attrs.get("name"))
        want_artist = normalize_text(attrs.get("artistName"))
        best, best_score = None, 0.0
        for track, title, artist in index:
            score = (fuzz.token_sort_ratio(want_title, title) * 0.7
                     + fuzz.token_set_ratio(want_artist, artist) * 0.3)
            if score > best_score:
                best, best_score = track, score
        if best and best_score >= MATCH_THRESHOLD:
            matched_ids.append(best.id)
        else:
            unmatched.append({"title": attrs.get("name"), "artist": attrs.get("artistName"),
                              "apple_id": item.get("id"), "url": attrs.get("url"),
                              "best_score": round(best_score, 1)})

    playlist = (await s.execute(
        select(Playlist).where(Playlist.external_id == playlist_id))).scalar_one_or_none()
    if playlist is None:
        playlist = Playlist(name=f"Apple - {playlist_id}", source="apple",
                            external_id=playlist_id)
        s.add(playlist)
    playlist.track_ids = matched_ids
    playlist.unmatched = unmatched
    playlist.synced_at = datetime.now(UTC).replace(tzinfo=None)
    await s.commit()

    return {"playlist_id": playlist_id, "total": len(items),
            "matched": len(matched_ids), "unmatched": len(unmatched),
            "unmatched_items": unmatched[:100]}


@router.post("/playlists/export")
async def export_playlist(req: ExportRequest,
                          s: AsyncSession = Depends(get_session)) -> dict:
    """Export a local selection to the user's Apple Music library."""
    provider = _provider(s)
    tracks = list((await s.execute(
        select(Track).where(Track.id.in_(req.track_ids)))).scalars().all())

    catalog_ids, unmatched = [], []
    try:
        for t in tracks:
            if t.apple_id:
                catalog_ids.append(t.apple_id)
                continue
            candidates = await provider.lookup(
                title=t.title, artist=t.artist or t.albumartist,
                album=t.album, duration=t.duration, isrc=t.isrc)
            if candidates and candidates[0].confidence >= 0.7 and candidates[0].apple_id:
                catalog_ids.append(candidates[0].apple_id)
                t.apple_id = candidates[0].apple_id
            else:
                unmatched.append({"track_id": t.id, "title": t.title, "artist": t.artist})

        if not catalog_ids:
            raise HTTPException(400, "no track could be matched to the Apple catalogue")

        created = await provider.create_playlist(req.name, req.description, catalog_ids)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await provider.aclose()

    await s.commit()
    return {"created": bool(created), "exported": len(catalog_ids),
            "unmatched": unmatched,
            "playlist": ((created or {}).get("data") or [{}])[0].get("id")}
