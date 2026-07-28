"""Discogs: strong on rare pressings, regional releases and non-Western catalogues."""
from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz

from app.providers.base import BaseProvider, TrackMetadata

log = logging.getLogger(__name__)

API = "https://api.discogs.com"
_YEAR = re.compile(r"(\d{4})")
_ARTIST_SUFFIX = re.compile(r"\s*\(\d+\)$")     # Discogs disambiguates as "Artist (2)"


class DiscogsProvider(BaseProvider):
    name = "discogs"
    rate_calls = 1          # 60 requests per minute for authenticated clients
    rate_period = 1.1
    cache_ttl_days = 120

    @property
    def enabled(self) -> bool:
        return bool(self.settings.discogs_token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Discogs token={self.settings.discogs_token}",
            "User-Agent": self.settings.ua_header(),
        }

    async def lookup(self, *, title=None, artist=None, album=None,
                     duration=None, **_) -> list[TrackMetadata]:
        if not self.enabled or not (album or title):
            return []

        params: dict[str, str] = {"type": "release", "per_page": "8"}
        if album:
            params["release_title"] = album
        if artist:
            params["artist"] = artist
        if not album and title:
            params["track"] = title

        data = await self.cached_json(
            ("search", artist or "", album or "", title or ""),
            f"{API}/database/search", params=params, headers=self._headers(),
        )
        results = (data or {}).get("results") or []
        out: list[TrackMetadata] = []

        for idx, row in enumerate(results[:4]):
            release_id = row.get("id")
            detail = await self._release(release_id) if release_id else None
            md = self._from_release(row, detail, title)
            # Discogs ranks by its own relevance; translate rank into confidence.
            md.confidence = round(max(0.35, 0.82 - idx * 0.12), 4)

            # The penalty used to compare the searched title against md.title --
            # which falls back to the searched title when no tracklist entry
            # matched. So it compared the input with itself, scored 1.0, and
            # could never reject anything. A release that lists its tracks and
            # does not contain ours got exactly the same confidence as one that
            # does.
            #
            # `title_matched` is False only when the release published a
            # tracklist and nothing in it resembled the track we asked about.
            # That is real evidence of the wrong release, so it is treated as
            # such rather than ignored.
            matched = md.extra.pop("title_matched", None) if md.extra else None
            if matched is False:
                md.confidence = round(md.confidence * 0.4, 4)
            elif matched is True and md.title and title:
                from app.core.dedup import normalize_text
                similarity = fuzz.token_sort_ratio(
                    normalize_text(title), normalize_text(md.title)) / 100
                md.confidence = round(md.confidence * (0.5 + 0.5 * similarity), 4)
            out.append(md)

        out.sort(key=lambda m: m.confidence, reverse=True)
        return out

    async def _release(self, release_id: int) -> dict | None:
        return await self.cached_json(
            ("release", release_id), f"{API}/releases/{release_id}", headers=self._headers())

    def _from_release(self, search_row: dict, detail: dict | None,
                      wanted_title: str | None) -> TrackMetadata:
        from app.core.dedup import normalize_text

        d = detail or {}
        artists = d.get("artists") or []
        artist_name = ", ".join(
            _ARTIST_SUFFIX.sub("", a.get("name", "")) for a in artists
        ) or (search_row.get("title", "").split(" - ")[0] or None)

        year = d.get("year") or None
        if not year:
            m = _YEAR.search(str(search_row.get("year") or ""))
            year = int(m.group(1)) if m else None

        track_no = total = None
        track_title = None
        tracklist = d.get("tracklist") or []
        if wanted_title and tracklist:
            wanted = normalize_text(wanted_title)
            best, best_similarity = None, 0
            for track in tracklist:
                similarity = fuzz.token_sort_ratio(wanted, normalize_text(track.get("title")))
                if similarity > best_similarity:
                    best, best_similarity = track, similarity
            if best and best_similarity >= 82:
                track_title = best.get("title")
                m = re.search(r"(\d+)", str(best.get("position") or ""))
                track_no = int(m.group(1)) if m else None
        if tracklist:
            total = sum(1 for t in tracklist if (t.get("type_") or "track") == "track")

        images = d.get("images") or []
        primary = next((i for i in images if i.get("type") == "primary"),
                       images[0] if images else None)
        genres = (d.get("genres") or []) + (d.get("styles") or [])
        formats = d.get("formats") or [{}]

        return TrackMetadata(
            source=self.name,
            title=track_title or wanted_title,
            artist=artist_name,
            albumartist=artist_name,
            album=d.get("title") or search_row.get("title"),
            year=int(year) if year else None,
            track_no=track_no,
            total_tracks=total,
            genre=genres[0] if genres else None,
            discogs_release_id=str(d.get("id") or search_row.get("id") or "") or None,
            artwork_url=(primary or {}).get("uri"),
            artwork_px=(primary or {}).get("width"),
            compilation=any("compilation" in desc.lower()
                            for desc in (formats[0].get("descriptions") or [])),
            extra={"country": d.get("country"),
                   "labels": [lbl.get("name") for lbl in (d.get("labels") or [])],
                   # None when the release published no tracklist to judge by;
                   # False when it did and our track was not in it.
                   "title_matched": bool(track_title) if tracklist else None},
        )
