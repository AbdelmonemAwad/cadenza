"""Lyrics from LRCLIB: synced and plain, no API key required."""
from __future__ import annotations

import logging
import re

from app.providers.base import BaseProvider, TrackMetadata

log = logging.getLogger(__name__)

API = "https://lrclib.net/api"
_LRC_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


class LyricsProvider(BaseProvider):
    name = "lrclib"
    rate_calls = 4
    rate_period = 1.0
    cache_ttl_days = 180

    async def lookup(self, *, title=None, artist=None, album=None,
                     duration=None, **_) -> list[TrackMetadata]:
        if not title or not artist:
            return []

        # The exact endpoint also matches on duration, which avoids picking up
        # live or extended versions with different timings.
        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = str(int(round(duration)))

        data = await self.cached_json(
            ("get", artist, title, album or "", int(duration or 0)),
            f"{API}/get", params=params)

        if not data:
            data = await self._search_fallback(title, artist, duration)
        if not data:
            return []

        synced = data.get("syncedLyrics") or None
        plain = data.get("plainLyrics") or None
        if synced and not plain:
            plain = strip_timestamps(synced)

        return [TrackMetadata(
            source=self.name,
            confidence=0.9 if synced else 0.7,
            title=data.get("trackName"), artist=data.get("artistName"),
            album=data.get("albumName"),
            lyrics=plain, synced_lyrics=synced,
            extra={"instrumental": data.get("instrumental", False),
                   "lrclib_id": data.get("id")},
        )]

    async def _search_fallback(self, title: str, artist: str,
                               duration: float | None) -> dict | None:
        rows = await self.cached_json(
            ("search", artist, title), f"{API}/search",
            params={"track_name": title, "artist_name": artist})
        if not rows:
            return None
        if duration:
            rows = sorted(rows, key=lambda r: abs((r.get("duration") or 0) - duration))
            if rows and abs((rows[0].get("duration") or 0) - duration) > 12:
                return None
        # Prefer a result that actually carries synced lyrics.
        ranked = sorted(rows, key=lambda r: 0 if r.get("syncedLyrics") else 1)
        return ranked[0] if ranked else None


def strip_timestamps(lrc: str) -> str:
    lines = []
    for line in lrc.splitlines():
        cleaned = re.sub(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def is_valid_lrc(text: str | None) -> bool:
    if not text:
        return False
    return sum(1 for line in text.splitlines() if _LRC_LINE.match(line)) >= 3
