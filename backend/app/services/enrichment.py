"""Enrichment service: gather metadata from all sources and write it to the file."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import artwork as artwork_mod
from app.core.quality import score_for_track
from app.core.tags import TagSet, read_tags, write_tags
from app.db.models import AuditLog, Track
from app.providers.aggregator import MergedResult, MetadataAggregator

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EnrichResult:
    track_id: int
    path: str
    applied: bool = False
    dry_run: bool = True
    confidence: float = 0.0
    changed_fields: dict[str, tuple] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    artwork: str | None = None
    lyrics: str | None = None
    error: str | None = None


class EnrichmentService:
    def __init__(self, session: AsyncSession,
                 aggregator: MetadataAggregator | None = None) -> None:
        self.s = get_settings()
        self.session = session
        self.aggregator = aggregator or MetadataAggregator(session)
        self._owns_aggregator = aggregator is None

    async def aclose(self) -> None:
        if self._owns_aggregator:
            await self.aggregator.aclose()

    async def enrich(self, track: Track, *, dry_run: bool = True,
                     overwrite: bool = False, min_confidence: float = 0.55,
                     want_artwork: bool = True, want_lyrics: bool = True,
                     job_id: int | None = None) -> EnrichResult:
        path = Path(track.path)
        result = EnrichResult(track_id=track.id, path=track.path, dry_run=dry_run)

        if not path.is_file():
            result.error = "file not found on disk"
            return result

        try:
            merged: MergedResult = await self.aggregator.identify(
                title=track.title, artist=track.artist or track.albumartist,
                album=track.album, duration=track.duration,
                fingerprint=track.fingerprint, isrc=track.isrc,
                mbid=track.mb_recording_id, want_lyrics=want_lyrics,
            )
        except Exception as exc:
            result.error = f"lookup failed: {exc}"
            log.exception("enrichment lookup failed for %s", track.path)
            return result

        result.confidence = merged.overall_confidence
        result.field_sources = merged.field_sources
        result.conflicts = merged.conflicts

        if merged.overall_confidence < min_confidence:
            result.error = (f"confidence too low "
                            f"({merged.overall_confidence:.2f} < {min_confidence:.2f}); "
                            "nothing was changed")
            return result

        current: TagSet = read_tags(path)
        proposed: TagSet = merged.to_tagset(
            base=TagSet(**{k: v for k, v in current.to_dict().items() if k != "extra"}),
            overwrite=overwrite,
        )

        for name in ("title", "artist", "albumartist", "album", "year", "track_no",
                     "total_tracks", "disc_no", "genre", "isrc", "composer",
                     "mb_recording_id", "mb_release_id", "acoustid", "apple_id"):
            old, new = getattr(current, name, None), getattr(proposed, name, None)
            if new not in (None, "", 0) and old != new:
                result.changed_fields[name] = (old, new)

        # ---- Artwork ----
        art_bytes: bytes | None = None
        art_mime = "image/jpeg"
        needs_artwork = want_artwork and (
            not current.has_artwork or (current.artwork_px or 0) < self.s.artwork_min_px)
        if needs_artwork:
            # Cache per release so an album is fetched once, not once per track.
            cache_key = hashlib.sha1(
                f"{merged.metadata.mb_release_id or ''}|{merged.metadata.album or ''}|"
                f"{merged.metadata.albumartist or ''}".encode()).hexdigest()
            cached = artwork_mod.cache_get(cache_key)
            if cached:
                art_bytes = cached
            else:
                fetched = await self.aggregator.fetch_artwork(merged.metadata)
                if fetched:
                    normalized = artwork_mod.normalize(fetched[0])
                    if normalized:
                        art_bytes, art_mime = normalized
                        artwork_mod.cache_put(cache_key, art_bytes)
            if art_bytes:
                dims = artwork_mod.dimensions(art_bytes)
                result.artwork = f"{dims[0]}x{dims[1]}" if dims else "fetched"

        # ---- Lyrics ----
        if want_lyrics and merged.metadata.synced_lyrics:
            result.lyrics = "synced"
        elif want_lyrics and merged.metadata.lyrics:
            result.lyrics = "plain"

        if dry_run:
            return result

        # ---- Apply ----
        try:
            if self.s.embed_lyrics:
                proposed.lyrics = merged.metadata.lyrics or proposed.lyrics
                proposed.synced_lyrics = merged.metadata.synced_lyrics or proposed.synced_lyrics

            write_tags(path, proposed,
                       artwork=art_bytes if self.s.embed_artwork else None,
                       artwork_mime=art_mime)

            if art_bytes and self.s.write_cover_file:
                artwork_mod.save_cover_file(path.parent, art_bytes)

            lrc = merged.metadata.synced_lyrics
            if lrc and self.s.write_lrc_file:
                path.with_suffix(".lrc").write_text(lrc, encoding="utf-8")

            # What was actually written, not what was downloaded. Artwork is
            # fetched whenever a provider has one, but only stored if
            # embed_artwork or write_cover_file is on -- and the row used to be
            # marked has_artwork on the fetch, so with both settings off every
            # enriched track claimed a cover it did not have. The dashboard's
            # "missing artwork" figure then dropped to zero while nothing had
            # changed on disk.
            art_stored = art_bytes if (
                art_bytes and (self.s.embed_artwork or self.s.write_cover_file)
            ) else None
            self._sync_track_row(track, proposed, art_stored, merged)
            self.session.add(AuditLog(
                action="enrich", level="info", track_id=track.id, job_id=job_id,
                src_path=track.path, reversible=False,
                detail={"confidence": merged.overall_confidence,
                        "sources": merged.field_sources,
                        "changed": {k: list(v) for k, v in result.changed_fields.items()}},
            ))
            result.applied = True
        except Exception as exc:
            result.error = f"write failed: {exc}"
            log.exception("failed to write tags for %s", track.path)
        return result

    def _sync_track_row(self, track: Track, tags: TagSet,
                        art: bytes | None, merged: MergedResult) -> None:
        track.title = tags.title or track.title
        track.artist = tags.artist or track.artist
        track.albumartist = tags.albumartist or track.albumartist
        track.album = tags.album or track.album
        track.year = tags.year or track.year
        track.track_no = tags.track_no or track.track_no
        track.disc_no = tags.disc_no or track.disc_no
        track.total_tracks = tags.total_tracks or track.total_tracks
        track.genre = tags.genre or track.genre
        track.isrc = tags.isrc or track.isrc
        track.mb_recording_id = tags.mb_recording_id or track.mb_recording_id
        track.mb_release_id = tags.mb_release_id or track.mb_release_id
        track.apple_id = merged.metadata.apple_id or track.apple_id
        track.acoustid = tags.acoustid or track.acoustid
        if art:
            track.has_artwork = True
            dims = artwork_mod.dimensions(art)
            track.artwork_px = dims[0] if dims else track.artwork_px
        if merged.metadata.synced_lyrics:
            track.has_lyrics = track.has_synced_lyrics = True
        elif merged.metadata.lyrics:
            track.has_lyrics = True
        track.tag_completeness = tags.completeness()
        # quality_score is 0.55*format + 0.15*rate + 0.10*depth + 0.20*tag
        # completeness, so changing the tags changes it. Updating one and not
        # the other left the dashboard's quality figure and every
        # quality-sorted view reflecting the tags as they were before
        # enrichment ran -- for ever, because nothing else recomputes it until
        # a full rescan.
        track.quality_score = score_for_track(track)
