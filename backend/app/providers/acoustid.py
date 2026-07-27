"""AcoustID: turns an acoustic fingerprint into a MusicBrainz recording id."""
from __future__ import annotations

import logging

from app.providers.base import BaseProvider, TrackMetadata

log = logging.getLogger(__name__)

API = "https://api.acoustid.org/v2/lookup"


class AcoustIDProvider(BaseProvider):
    name = "acoustid"
    rate_calls = 3          # documented limit: 3 requests per second
    rate_period = 1.0
    cache_ttl_days = 180    # a fingerprint never changes, so cache aggressively

    @property
    def enabled(self) -> bool:
        return bool(self.settings.acoustid_api_key)

    async def lookup(self, *, fingerprint: str | None = None, duration: float | None = None,
                     **_) -> list[TrackMetadata]:
        if not self.enabled or not fingerprint or not duration:
            return []

        params = {
            "client": self.settings.acoustid_api_key,
            "format": "json",
            "duration": str(int(round(duration))),
            "fingerprint": fingerprint,
            "meta": "recordings releasegroups releases tracks compress",
        }
        data = await self.cached_json(
            ("lookup", fingerprint[:64], int(round(duration))), API, params=params)

        if not data or data.get("status") != "ok":
            return []

        out: list[TrackMetadata] = []
        for result in data.get("results", [])[:5]:
            base_score = float(result.get("score") or 0.0)
            acoustid_id = result.get("id")

            recordings = result.get("recordings") or []
            if not recordings:
                out.append(TrackMetadata(
                    source=self.name, confidence=base_score * 0.5, acoustid=acoustid_id))
                continue

            for rec in recordings[:4]:
                artists = rec.get("artists") or []
                release, release_group = self._pick_release(rec)

                out.append(TrackMetadata(
                    source=self.name,
                    confidence=round(base_score, 4),
                    title=rec.get("title"),
                    artist=" & ".join(a.get("name", "") for a in artists) or None,
                    album=(release or {}).get("title") or (release_group or {}).get("title"),
                    year=self._year(release),
                    track_no=self._track_no(release),
                    total_tracks=self._total_tracks(release),
                    mb_recording_id=rec.get("id"),
                    mb_release_id=(release or {}).get("id"),
                    mb_artist_id=artists[0].get("id") if artists else None,
                    acoustid=acoustid_id,
                    extra={"duration_hint": rec.get("duration")},
                ))
        out.sort(key=lambda m: m.confidence, reverse=True)
        return out

    # ---- Extraction helpers ----

    @staticmethod
    def _pick_release(rec: dict) -> tuple[dict | None, dict | None]:
        """Prefer the earliest release: closer to the original album than a compilation."""
        releases = rec.get("releases") or []
        groups = rec.get("releasegroups") or []
        if not releases:
            return None, groups[0] if groups else None

        def sort_key(r: dict):
            d = r.get("date") or {}
            return (d.get("year") or 9999, d.get("month") or 12, d.get("day") or 31)

        return sorted(releases, key=sort_key)[0], (groups[0] if groups else None)

    @staticmethod
    def _year(release: dict | None) -> int | None:
        if not release:
            return None
        y = (release.get("date") or {}).get("year")
        return int(y) if y else None

    @staticmethod
    def _track_no(release: dict | None) -> int | None:
        if not release:
            return None
        for medium in release.get("mediums") or []:
            for track in medium.get("tracks") or []:
                if track.get("position"):
                    return int(track["position"])
        return None

    @staticmethod
    def _total_tracks(release: dict | None) -> int | None:
        if not release:
            return None
        for medium in release.get("mediums") or []:
            if medium.get("track_count"):
                return int(medium["track_count"])
        return None
