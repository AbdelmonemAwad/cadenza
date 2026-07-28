"""Multi-source metadata aggregation.

Strategy:
  1) Identify first. An acoustic fingerprint resolved through AcoustID to a
     MusicBrainz id beats any textual search.
  2) Query the remaining sources in parallel; each returns scored candidates.
  3) Vote per *field*, not per source: each field is won by the source with the
     highest (match confidence x that source's trust for that field). Discogs
     knows pressing years, Apple has the best artwork, MusicBrainz owns the
     identifiers, Last.fm is best for genre.
  4) Existing file tags are never overwritten unless the replacement clears an
     explicit confidence threshold.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.dedup import normalize_text
from app.core.tags import TagSet, image_width
from app.providers.acoustid import AcoustIDProvider
from app.providers.applemusic import AppleMusicProvider
from app.providers.base import BaseProvider, TrackMetadata
from app.providers.discogs import DiscogsProvider
from app.providers.lastfm import LastFmProvider
from app.providers.lyrics import LyricsProvider
from app.providers.musicbrainz import MusicBrainzProvider

log = logging.getLogger(__name__)

# Per-field trust for each source (0..1). This table is the heart of the merge.
FIELD_TRUST: dict[str, dict[str, float]] = {
    "title":            {"musicbrainz": 0.95, "applemusic": 0.92, "discogs": 0.80, "lastfm": 0.70, "acoustid": 0.88},
    "artist":           {"musicbrainz": 0.95, "applemusic": 0.90, "discogs": 0.85, "lastfm": 0.72, "acoustid": 0.85},
    "albumartist":      {"musicbrainz": 0.93, "applemusic": 0.85, "discogs": 0.88, "lastfm": 0.60, "acoustid": 0.75},
    "album":            {"musicbrainz": 0.92, "applemusic": 0.90, "discogs": 0.88, "lastfm": 0.68, "acoustid": 0.82},
    "year":             {"musicbrainz": 0.88, "applemusic": 0.75, "discogs": 0.93, "lastfm": 0.40, "acoustid": 0.80},
    "date":             {"musicbrainz": 0.88, "applemusic": 0.80, "discogs": 0.85, "lastfm": 0.35, "acoustid": 0.75},
    "track_no":         {"musicbrainz": 0.90, "applemusic": 0.92, "discogs": 0.86, "lastfm": 0.50, "acoustid": 0.78},
    "total_tracks":     {"musicbrainz": 0.90, "applemusic": 0.80, "discogs": 0.88, "lastfm": 0.40, "acoustid": 0.78},
    "disc_no":          {"musicbrainz": 0.90, "applemusic": 0.88, "discogs": 0.82, "lastfm": 0.30, "acoustid": 0.75},
    "genre":            {"musicbrainz": 0.70, "applemusic": 0.85, "discogs": 0.90, "lastfm": 0.88, "acoustid": 0.55},
    "composer":         {"musicbrainz": 0.80, "applemusic": 0.88, "discogs": 0.75, "lastfm": 0.30, "acoustid": 0.60},
    "isrc":             {"musicbrainz": 0.95, "applemusic": 0.97, "discogs": 0.50, "lastfm": 0.20, "acoustid": 0.85},
    "artwork_url":      {"applemusic": 0.96, "musicbrainz": 0.85, "discogs": 0.80, "lastfm": 0.55, "acoustid": 0.40},
    "mb_recording_id":  {"acoustid": 0.97, "musicbrainz": 0.99, "lastfm": 0.55},
    "mb_release_id":    {"acoustid": 0.90, "musicbrainz": 0.99},
    "mb_artist_id":     {"acoustid": 0.88, "musicbrainz": 0.99},
    "apple_id":         {"applemusic": 1.0},
    "discogs_release_id": {"discogs": 1.0},
    "compilation":      {"musicbrainz": 0.88, "discogs": 0.85, "applemusic": 0.70, "acoustid": 0.60},
}

MERGE_FIELDS = tuple(FIELD_TRUST.keys())

# Minimum field weight required to replace a value that already exists in the file.
OVERWRITE_THRESHOLD = 0.80


@dataclass(slots=True)
class FieldChoice:
    value: object
    source: str
    weight: float


@dataclass(slots=True)
class MergedResult:
    metadata: TrackMetadata = field(default_factory=TrackMetadata)
    field_sources: dict[str, str] = field(default_factory=dict)
    field_weights: dict[str, float] = field(default_factory=dict)
    candidates_by_source: dict[str, int] = field(default_factory=dict)
    overall_confidence: float = 0.0
    conflicts: dict[str, list[str]] = field(default_factory=dict)

    def to_tagset(self, base: TagSet | None = None, overwrite: bool = False) -> TagSet:
        """Project the merge onto a writable TagSet, respecting existing tags."""
        t = base or TagSet()
        md = self.metadata
        for name in ("title", "artist", "albumartist", "album", "year", "date",
                     "track_no", "total_tracks", "disc_no", "total_discs", "genre",
                     "composer", "isrc", "compilation", "mb_recording_id",
                     "mb_release_id", "mb_artist_id", "acoustid", "apple_id",
                     "lyrics", "synced_lyrics"):
            new = getattr(md, name, None)
            if new in (None, "", 0, False):
                continue

            file_has_value = getattr(t, name, None) not in (None, "", 0, False)
            # Filling a gap is always safe. Replacing something the user (or a
            # previous tagger) already set requires both an explicit opt-in and
            # a high-trust source.
            confident = self.field_weights.get(name, 0.0) >= OVERWRITE_THRESHOLD
            if not file_has_value or (overwrite and confident):
                setattr(t, name, new)
        return t


class MetadataAggregator:
    def __init__(self, session: AsyncSession | None = None,
                 apple_user_token: str | None = None) -> None:
        self.settings = get_settings()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": self.settings.ua_header()},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        self.acoustid = AcoustIDProvider(session, self._client)
        self.musicbrainz = MusicBrainzProvider(session, self._client)
        self.applemusic = AppleMusicProvider(session, self._client, user_token=apple_user_token)
        self.discogs = DiscogsProvider(session, self._client)
        self.lastfm = LastFmProvider(session, self._client)
        self.lyrics = LyricsProvider(session, self._client)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MetadataAggregator:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    @property
    def secondary_providers(self) -> list[BaseProvider]:
        by_name = {
            "musicbrainz": self.musicbrainz, "applemusic": self.applemusic,
            "discogs": self.discogs, "lastfm": self.lastfm,
        }
        return [by_name[n] for n in self.settings.provider_order
                if n in by_name and by_name[n].enabled]

    # --------------------------- Main entry point ---------------------------

    async def identify(self, *, title: str | None, artist: str | None,
                       album: str | None = None, duration: float | None = None,
                       fingerprint: str | None = None, isrc: str | None = None,
                       mbid: str | None = None,
                       want_lyrics: bool = True) -> MergedResult:

        # 1) Identity via acoustic fingerprint: the most precise starting point.
        seed: list[TrackMetadata] = []
        if fingerprint and duration and self.acoustid.enabled and not mbid:
            try:
                seed = await self.acoustid.lookup(fingerprint=fingerprint, duration=duration)
            except Exception as exc:
                log.warning("acoustid lookup failed: %s", exc)
            if seed and seed[0].mb_recording_id and seed[0].confidence >= 0.85:
                mbid = seed[0].mb_recording_id
                title = title or seed[0].title
                artist = artist or seed[0].artist

        # 2) Remaining sources in parallel.
        tasks = [
            self._safe(p, title=title, artist=artist, album=album,
                       duration=duration, isrc=isrc,
                       mbid=mbid if p.name == "musicbrainz" else None)
            for p in self.secondary_providers
        ]
        if want_lyrics and title and artist:
            tasks.append(self._safe(self.lyrics, title=title, artist=artist,
                                    album=album, duration=duration))

        results: list[list[TrackMetadata]] = await asyncio.gather(*tasks)
        pool: list[TrackMetadata] = list(seed)
        for group in results:
            pool.extend(group)

        return self.merge(pool, ref_title=title, ref_artist=artist)

    async def _safe(self, provider: BaseProvider, **kw) -> list[TrackMetadata]:
        """One failing provider must not sink the whole lookup."""
        try:
            return await provider.lookup(**kw)
        except Exception as exc:
            log.warning("provider %s failed: %s", provider.name, exc)
            return []

    # ------------------------------ Merging ------------------------------

    def merge(self, candidates: Sequence[TrackMetadata], *,
              ref_title: str | None = None,
              ref_artist: str | None = None) -> MergedResult:
        result = MergedResult()
        if not candidates:
            return result

        # Keep the single best candidate per source: never blend two
        # contradictory answers from the same provider.
        best_per_source: dict[str, TrackMetadata] = {}
        for c in candidates:
            c.confidence = self._adjusted_confidence(c, ref_title, ref_artist)
            result.candidates_by_source[c.source] = \
                result.candidates_by_source.get(c.source, 0) + 1
            current = best_per_source.get(c.source)
            if current is None or c.confidence > current.confidence:
                best_per_source[c.source] = c

        chosen = list(best_per_source.values())

        for name in MERGE_FIELDS:
            trust_map = FIELD_TRUST.get(name, {})
            options: list[FieldChoice] = []
            for c in chosen:
                value = getattr(c, name, None)
                if value in (None, "", 0) or (name == "compilation" and value is False):
                    continue
                trust = trust_map.get(c.source, 0.30)
                if trust <= 0:
                    continue
                options.append(FieldChoice(value, c.source, round(trust * c.confidence, 5)))
            if not options:
                continue

            options.sort(key=lambda o: o.weight, reverse=True)
            winner = options[0]
            setattr(result.metadata, name, winner.value)
            result.field_sources[name] = winner.source
            result.field_weights[name] = winner.weight

            distinct = {self._conflict_key(o.value) for o in options}
            if len(distinct) > 1:
                result.conflicts[name] = [
                    f"{o.source}={o.value!r}({o.weight:.2f})" for o in options[:4]
                ]

        # `year` and `date` are voted independently, with different trust maps
        # -- Discogs is trusted most for the pressing year and less for the
        # full date, deliberately. So the two can be decided from different
        # sources and disagree, and then they are written to different places:
        # the database and the enrichment report show `year`, while the file
        # gets `TDRC` from `date or year` and ends up with the other one. The
        # user reads 1973 in Cadenza and 1972 in every player.
        #
        # The year vote wins, because it is the value the interface shows and
        # the one whose trust map was tuned for this. A date that disagrees
        # with it is dropped rather than silently rewritten, so nothing claims
        # a precision it did not vote for.
        year = result.metadata.year
        date = result.metadata.date
        if year and date:
            date_year = str(date)[:4]
            if date_year.isdigit() and int(date_year) != int(year):
                log.debug("dropping date %r: it disagrees with the voted year %s",
                          date, year)
                result.metadata.date = None
                result.conflicts.setdefault("date", []).append(
                    f"dropped {date!r}: disagrees with year={year}")

        # Lyrics come from a dedicated provider and skip the vote entirely.
        for c in candidates:
            if c.source == "lrclib":
                result.metadata.lyrics = c.lyrics
                result.metadata.synced_lyrics = c.synced_lyrics
                result.field_sources["lyrics"] = "lrclib"
                break

        acoustid_value = next((c.acoustid for c in candidates if c.acoustid), None)
        if acoustid_value:
            result.metadata.acoustid = acoustid_value

        result.metadata.source = "+".join(sorted(best_per_source))
        core_weights = [w for f, w in result.field_weights.items()
                        if f in ("title", "artist", "album", "year")]
        result.overall_confidence = (
            round(sum(core_weights) / len(core_weights), 4) if core_weights else 0.0)
        return result

    @staticmethod
    def _adjusted_confidence(c: TrackMetadata, ref_title: str | None,
                             ref_artist: str | None) -> float:
        """Penalise candidates that drift away from the file's existing tags."""
        confidence = c.confidence or 0.5
        if ref_title and c.title:
            similarity = fuzz.token_sort_ratio(
                normalize_text(ref_title), normalize_text(c.title)) / 100
            confidence *= 0.45 + 0.55 * similarity
        if ref_artist and c.artist:
            similarity = fuzz.token_set_ratio(
                normalize_text(ref_artist), normalize_text(c.artist)) / 100
            confidence *= 0.60 + 0.40 * similarity
        return round(min(1.0, max(0.0, confidence)), 5)

    @staticmethod
    def _conflict_key(v: object) -> str:
        return normalize_text(str(v)) if isinstance(v, str) else str(v)

    # ------------------------------ Artwork ------------------------------

    async def fetch_artwork(self, md: TrackMetadata,
                            min_px: int | None = None) -> tuple[bytes, str] | None:
        """Fetch the highest resolution available, trying sources in order."""
        min_px = min_px or self.settings.artwork_min_px
        urls: list[str] = []
        if md.artwork_url:
            urls.append(md.artwork_url)
        if md.mb_release_id:
            caa = await self.musicbrainz.cover_art_url(
                md.mb_release_id, self.settings.artwork_target_px)
            if caa:
                urls.append(caa)

        for index, url in enumerate(urls):
            is_last = index == len(urls) - 1
            try:
                r = await self._client.get(url, timeout=30.0)
                if r.status_code != 200 or not r.content:
                    continue
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if not mime.startswith("image/"):
                    continue
                px = image_width(r.content) or 0
                if px and px < min_px and not is_last:
                    continue          # try a higher-resolution source first
                return r.content, mime
            except httpx.HTTPError as exc:
                log.debug("artwork fetch failed %s: %s", url, exc)
        return None
