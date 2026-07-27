"""Incremental library scanner: index files and re-read only what changed."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AUDIO_EXTENSIONS, get_settings
from app.core import audio_probe, fingerprint, hashing
from app.core.tags import read_tags
from app.db.models import Track, TrackStatus

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], Awaitable[None]] | None

# Synology sprinkles these throughout shared folders; indexing them is noise.
_SKIP_DIRS = {"@eaDir", "#recycle", ".DS_Store", "@tmp", "#snapshot", ".cadenza"}


@dataclass(slots=True)
class ScanStats:
    found: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    corrupt: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def iter_audio_files(root: Path, follow_symlinks: bool = False,
                     skip_hidden: bool = True) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not (skip_hidden and d.startswith("."))
        ]
        for fn in filenames:
            if skip_hidden and fn.startswith("."):
                continue
            if Path(fn).suffix.lower() in AUDIO_EXTENSIONS:
                yield Path(dirpath) / fn


class LibraryScanner:
    def __init__(self, session: AsyncSession, progress: ProgressCb = None) -> None:
        self.s = get_settings()
        self.session = session
        self.progress = progress
        self.stats = ScanStats()

    async def scan(self, root: Path | None = None, *, full: bool = False,
                   compute_fingerprints: bool = True,
                   compute_audio_md5: bool = True) -> ScanStats:
        root = root or self.s.music_root
        if not root.is_dir():
            raise FileNotFoundError(f"music root not found: {root}")

        files = await asyncio.to_thread(
            lambda: list(iter_audio_files(root, self.s.follow_symlinks, self.s.skip_hidden)))
        self.stats.found = len(files)

        existing = {
            row.path: row for row in (await self.session.execute(
                select(Track).where(Track.path.startswith(str(root)))
            )).scalars().all()
        }
        seen: set[str] = set()
        sem = asyncio.Semaphore(max(1, self.s.workers))

        async def handle(idx: int, path: Path) -> None:
            async with sem:
                try:
                    await self._process(path, existing, full,
                                        compute_fingerprints, compute_audio_md5)
                except Exception as exc:
                    self.stats.errors += 1
                    log.exception("scan failed for %s: %s", path, exc)
                seen.add(str(path))
                if self.progress and idx % 25 == 0:
                    await self.progress(idx, len(files), str(path))

        # Commit in batches to bound memory on libraries with 100k+ files.
        batch = 200
        for start in range(0, len(files), batch):
            chunk = files[start:start + batch]
            await asyncio.gather(*(handle(start + i, p) for i, p in enumerate(chunk)))
            await self.session.commit()

        # Anything indexed but not seen this pass has disappeared from disk.
        for path, row in existing.items():
            if path not in seen and row.status == TrackStatus.ACTIVE:
                row.status = TrackStatus.MISSING
                self.stats.missing += 1
        await self.session.commit()

        if self.progress:
            await self.progress(len(files), len(files), "done")
        return self.stats

    async def _process(self, path: Path, existing: dict[str, Track], full: bool,
                       do_fingerprint: bool, do_md5: bool) -> None:
        st = path.stat()
        key = str(path)
        row = existing.get(key)

        # Incremental fast path: same size and mtime means nothing changed.
        if row is not None and not full and row.size_bytes == st.st_size \
                and abs((row.mtime or 0) - st.st_mtime) < 1.0 and row.sha256:
            if row.status == TrackStatus.MISSING:
                row.status = TrackStatus.ACTIVE
            row.last_scan = datetime.now(UTC)
            self.stats.unchanged += 1
            return

        if st.st_size < self.s.min_file_bytes:
            await self._mark_corrupt(row, path, st, "file too small")
            return

        info = await audio_probe.probe_async(path)
        if info.corrupt:
            await self._mark_corrupt(row, path, st, info.error or "probe failed")
            return

        tags = await asyncio.to_thread(read_tags, path)
        sha = await hashing.sha256_file_async(path)
        audio_md5 = await hashing.audio_stream_md5_async(path) if do_md5 else None

        fingerprint_text = None
        if do_fingerprint and self.s.acoustic_enabled:
            fpr = await fingerprint.compute_async(path)
            fingerprint_text = fpr.fingerprint
            if fpr.error:
                log.debug("fingerprint failed %s: %s", path.name, fpr.error)

        is_new = row is None
        if is_new:
            row = Track(path=key)
            self.session.add(row)

        row.filename = path.name
        row.ext = path.suffix.lower()
        row.size_bytes = st.st_size
        row.mtime = st.st_mtime
        row.inode = getattr(st, "st_ino", None)

        row.codec = info.codec
        row.lossless = info.lossless
        row.bitrate = info.bitrate
        row.sample_rate = info.sample_rate
        row.bit_depth = info.bit_depth
        row.channels = info.channels
        row.duration = info.duration

        row.sha256 = sha
        row.audio_md5 = audio_md5
        row.fingerprint = fingerprint_text
        row.acoustid = tags.acoustid or row.acoustid

        row.title = tags.title or path.stem
        row.artist = tags.artist
        row.albumartist = tags.albumartist or tags.artist
        row.album = tags.album
        row.year = tags.year
        row.track_no = tags.track_no
        row.disc_no = tags.disc_no
        row.total_tracks = tags.total_tracks
        row.genre = tags.genre
        row.isrc = tags.isrc
        row.mb_recording_id = tags.mb_recording_id
        row.mb_release_id = tags.mb_release_id
        row.apple_id = tags.apple_id

        row.has_artwork = tags.has_artwork or (path.parent / self.s.cover_filename).is_file()
        row.artwork_px = tags.artwork_px
        row.has_lyrics = bool(tags.lyrics) or path.with_suffix(".lrc").is_file()
        row.has_synced_lyrics = bool(tags.synced_lyrics) or path.with_suffix(".lrc").is_file()

        row.tag_completeness = tags.completeness()
        row.quality_score = self._quality(info, row.tag_completeness)
        row.status = TrackStatus.ACTIVE
        row.error = None
        row.last_scan = datetime.now(UTC)
        row.raw_tags = dict(tags.extra or {}) or None

        if is_new:
            self.stats.added += 1
        else:
            self.stats.updated += 1

    async def _mark_corrupt(self, row: Track | None, path: Path, st, reason: str) -> None:
        if row is None:
            row = Track(path=str(path), filename=path.name, ext=path.suffix.lower())
            self.session.add(row)
        row.size_bytes = st.st_size
        row.mtime = st.st_mtime
        row.status = TrackStatus.CORRUPT
        row.error = reason
        row.last_scan = datetime.now(UTC)
        self.stats.corrupt += 1

    @staticmethod
    def _quality(info: audio_probe.AudioInfo, tag_completeness: float) -> float:
        """Single 0..1 quality figure used for dashboard stats and sorting."""
        fmt = 1.0 if info.lossless else min((info.bitrate or 0) / 320_000.0, 1.0)
        sample_rate = min((info.sample_rate or 44100) / 96_000.0, 1.0)
        depth = min((info.bit_depth or 16) / 24.0, 1.0)
        return round(0.55 * fmt + 0.15 * sample_rate + 0.10 * depth
                     + 0.20 * tag_completeness, 4)
