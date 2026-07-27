"""MusicBrainz and Cover Art Archive: the primary reference source."""
from __future__ import annotations

import logging
import re

from app.providers.base import BaseProvider, TrackMetadata

log = logging.getLogger(__name__)

WS = "https://musicbrainz.org/ws/2"
COVER_ART_ARCHIVE = "https://coverartarchive.org"


def _lucene_escape(s: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", s)


class MusicBrainzProvider(BaseProvider):
    name = "musicbrainz"
    rate_calls = 1              # their policy: one request per second for anonymous clients
    rate_period = 1.05
    cache_ttl_days = 90

    async def lookup(self, *, title=None, artist=None, album=None, duration=None,
                     mbid=None, isrc=None, **_) -> list[TrackMetadata]:
        if mbid:
            m = await self.by_recording_id(mbid)
            return [m] if m else []
        if isrc:
            found = await self.by_isrc(isrc)
            if found:
                return found
        if not title:
            return []
        return await self.search(title=title, artist=artist, album=album, duration=duration)

    # ---- Search ----

    async def search(self, *, title: str, artist: str | None = None,
                     album: str | None = None, duration: float | None = None,
                     limit: int = 8) -> list[TrackMetadata]:
        terms = [f'recording:"{_lucene_escape(title)}"']
        if artist:
            terms.append(f'artist:"{_lucene_escape(artist)}"')
        if album:
            terms.append(f'release:"{_lucene_escape(album)}"')
        if duration:
            ms = int(duration * 1000)
            terms.append(f"dur:[{ms - 8000} TO {ms + 8000}]")
        query = " AND ".join(terms)

        data = await self.cached_json(
            ("search", query, limit),
            f"{WS}/recording",
            params={"query": query, "fmt": "json", "limit": limit,
                    "inc": "artist-credits+releases+isrcs"},
        )
        if not data:
            return []

        out = []
        top = max((r.get("score", 0) for r in data.get("recordings", [])), default=100) or 100
        for rec in data.get("recordings", []):
            md = self._from_recording(rec)
            md.confidence = round(min(1.0, (rec.get("score", 0) / top) * 0.95), 4)
            if duration and rec.get("length"):
                delta = abs(rec["length"] / 1000 - duration)
                if delta > 12:
                    md.confidence *= 0.55        # length disagrees badly, distrust it
                elif delta < 2:
                    md.confidence = min(1.0, md.confidence * 1.05)
            out.append(md)
        out.sort(key=lambda m: m.confidence, reverse=True)
        return out

    async def by_recording_id(self, mbid: str) -> TrackMetadata | None:
        data = await self.cached_json(
            ("recording", mbid), f"{WS}/recording/{mbid}",
            params={"fmt": "json", "inc": "artist-credits+releases+isrcs+genres+tags"},
        )
        if not data:
            return None
        md = self._from_recording(data)
        md.confidence = 1.0
        return md

    async def by_isrc(self, isrc: str) -> list[TrackMetadata]:
        data = await self.cached_json(
            ("isrc", isrc), f"{WS}/isrc/{isrc}",
            params={"fmt": "json", "inc": "artist-credits+releases"},
        )
        if not data:
            return []
        out = []
        for rec in data.get("recordings", []):
            md = self._from_recording(rec)
            md.isrc = isrc
            md.confidence = 0.98            # ISRC is a global identifier: very high trust
            out.append(md)
        return out

    async def release_details(self, release_id: str) -> dict | None:
        return await self.cached_json(
            ("release", release_id), f"{WS}/release/{release_id}",
            params={"fmt": "json", "inc": "recordings+artist-credits+labels+release-groups"},
        )

    async def missing_tracks(self, release_id: str, present_titles: set[str]) -> list[dict]:
        """List album tracks that are absent from the local library."""
        release = await self.release_details(release_id)
        if not release:
            return []
        missing = []
        for medium in release.get("media", []):
            for track in medium.get("tracks", []):
                title = (track.get("title") or "").strip()
                if title and title.lower() not in present_titles:
                    missing.append({
                        "position": track.get("position"),
                        "disc": medium.get("position"),
                        "title": title,
                        "length_ms": track.get("length"),
                        "recording_id": (track.get("recording") or {}).get("id"),
                    })
        return missing

    # ---- Cover art ----

    async def cover_art_url(self, release_id: str, size: int = 1200) -> str | None:
        """Cover Art Archive serves 250/500/1200 thumbnails, plus the original."""
        data = await self.cached_json(
            ("caa", release_id), f"{COVER_ART_ARCHIVE}/release/{release_id}")
        if not data:
            return None
        images = data.get("images") or []
        front = next((i for i in images if i.get("front")), images[0] if images else None)
        if not front:
            return None
        thumbnails = front.get("thumbnails") or {}
        for key in (str(size), "1200", "large", "500"):
            if thumbnails.get(key):
                return thumbnails[key]
        return front.get("image")

    # ---- Conversion ----

    def _from_recording(self, rec: dict) -> TrackMetadata:
        credits = rec.get("artist-credit") or []
        artist = "".join(
            (c.get("name") or (c.get("artist") or {}).get("name") or "")
            + (c.get("joinphrase") or "")
            for c in credits
        ).strip() or None
        artist_id = (credits[0].get("artist") or {}).get("id") if credits else None

        release = self._best_release(rec.get("releases") or [])
        year = track_no = total_tracks = disc_no = None
        album = albumartist = None
        compilation = False

        if release:
            album = release.get("title")
            date = release.get("date") or ""
            year = int(date[:4]) if date[:4].isdigit() else None
            release_credits = release.get("artist-credit") or []
            albumartist = release_credits[0].get("name") if release_credits else None
            group = release.get("release-group") or {}
            compilation = "Compilation" in (group.get("secondary-types") or [])
            for medium in release.get("media") or []:
                disc_no = medium.get("position") or disc_no
                total_tracks = medium.get("track-count") or total_tracks
                for track in medium.get("track") or medium.get("tracks") or []:
                    track_no = track.get("position") or track_no

        genres = rec.get("genres") or []
        isrcs = rec.get("isrcs") or []

        return TrackMetadata(
            source=self.name,
            title=rec.get("title"),
            artist=artist,
            albumartist=albumartist or artist,
            album=album,
            year=year,
            date=(release or {}).get("date"),
            track_no=track_no,
            total_tracks=total_tracks,
            disc_no=disc_no,
            genre=genres[0]["name"].title() if genres else None,
            isrc=isrcs[0] if isrcs else None,
            compilation=compilation,
            mb_recording_id=rec.get("id"),
            mb_release_id=(release or {}).get("id"),
            mb_artist_id=artist_id,
            extra={"length_ms": rec.get("length")},
        )

    @staticmethod
    def _best_release(releases: list[dict]) -> dict | None:
        """Prefer the earliest official release: closest to the original album."""
        if not releases:
            return None
        official = [r for r in releases if r.get("status") == "Official"] or releases
        return sorted(official, key=lambda r: (r.get("date") or "9999"))[0]
