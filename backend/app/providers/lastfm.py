"""Last.fm: genre tags, fallback artwork and spelling correction for artist names."""
from __future__ import annotations

import logging

from app.providers.base import BaseProvider, TrackMetadata

log = logging.getLogger(__name__)

API = "https://ws.audioscrobbler.com/2.0/"


class LastFmProvider(BaseProvider):
    name = "lastfm"
    rate_calls = 5
    rate_period = 1.0
    cache_ttl_days = 60

    @property
    def enabled(self) -> bool:
        return bool(self.settings.lastfm_api_key)

    async def lookup(self, *, title=None, artist=None, album=None, **_) -> list[TrackMetadata]:
        if not self.enabled or not title:
            return []

        # autocorrect=1 is valuable here: it repairs misspelled artist names
        # that would otherwise return nothing.
        params = {
            "method": "track.getInfo", "api_key": self.settings.lastfm_api_key,
            "track": title, "format": "json", "autocorrect": "1",
        }
        if artist:
            params["artist"] = artist

        data = await self.cached_json(("track", artist or "", title), API, params=params)
        track = (data or {}).get("track")
        if not track:
            return await self._search(title, artist)

        album_obj = track.get("album") or {}
        tags = ((track.get("toptags") or {}).get("tag")) or []
        position = (album_obj.get("@attr") or {}).get("position", "")

        return [TrackMetadata(
            source=self.name,
            confidence=0.62,          # supporting source, never authoritative
            title=track.get("name"),
            artist=(track.get("artist") or {}).get("name"),
            albumartist=album_obj.get("artist"),
            album=album_obj.get("title"),
            track_no=int(position) if str(position).isdigit() else None,
            genre=(tags[0].get("name") or "").title() if tags else None,
            mb_recording_id=track.get("mbid") or None,
            artwork_url=self._largest_image(album_obj.get("image") or []),
            extra={"listeners": track.get("listeners"), "playcount": track.get("playcount"),
                   "tags": [t.get("name") for t in tags[:6]]},
        )]

    async def _search(self, title: str, artist: str | None) -> list[TrackMetadata]:
        params = {
            "method": "track.search", "api_key": self.settings.lastfm_api_key,
            "track": title, "format": "json", "limit": "5",
        }
        if artist:
            params["artist"] = artist
        data = await self.cached_json(("search", artist or "", title), API, params=params)
        matches = (((data or {}).get("results") or {}).get("trackmatches") or {}).get("track") or []
        if isinstance(matches, dict):
            matches = [matches]
        return [
            TrackMetadata(
                source=self.name, confidence=round(max(0.25, 0.55 - i * 0.08), 4),
                title=m.get("name"), artist=m.get("artist"),
                mb_recording_id=m.get("mbid") or None,
                artwork_url=self._largest_image(m.get("image") or []),
            )
            for i, m in enumerate(matches[:5])
        ]

    async def album_info(self, artist: str, album: str) -> dict | None:
        return await self.cached_json(
            ("album", artist, album), API,
            params={"method": "album.getInfo", "api_key": self.settings.lastfm_api_key,
                    "artist": artist, "album": album, "format": "json", "autocorrect": "1"},
        )

    @staticmethod
    def _largest_image(images: list[dict]) -> str | None:
        order = {"mega": 5, "extralarge": 4, "large": 3, "medium": 2, "small": 1}
        best, rank = None, -1
        for img in images:
            r = order.get(img.get("size", ""), 0)
            if r > rank and img.get("#text"):
                best, rank = img["#text"], r
        return best
