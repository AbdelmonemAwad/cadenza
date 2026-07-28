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
# Returns True when the caller wants the scan to stop. Consulted between
# batches, so a cancelled scan keeps everything it has already indexed.
StopCb = Callable[[], bool] | None

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
    # True when the caller asked to stop before every file had been visited.
    # Reported so the summary does not read like a complete pass over a library
    # that was only half walked -- and so the missing sweep can be skipped.
    stopped: bool = False

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
    def __init__(self, session: AsyncSession, progress: ProgressCb = None,
                 should_stop: StopCb = None) -> None:
        self.s = get_settings()
        self.session = session
        self.progress = progress
        self.should_stop = should_stop
        self.stats = ScanStats()

    async def scan(self, root: Path | None = None, *, full: bool = False,
                   compute_fingerprints: bool = False,
                   compute_audio_md5: bool = False) -> ScanStats:
        """Index the library.

        The two expensive flags default to off, and that is the difference
        between a library you can browse in two minutes and one you wait forty
        minutes for. Measured on a DS1821+ over 3801 real files:

            ffprobe      64ms     2.5%
            read tags    19ms     0.7%
            sha256       79ms     3.1%
            audio_md5  1705ms    67.0%     <- decodes the whole file to PCM
            fingerprint 678ms    26.6%     <- decodes 120s of it again

        94% of a scan went on two figures that nothing needs in order to
        browse, search, sort by quality or organise. They exist for duplicate
        detection alone -- `find_exact_audio` groups by audio_md5 and
        `find_acoustic` needs the fingerprint -- so they now belong to a
        separate pass that runs afterwards, can be stopped, and picks up where
        it left off.
        """
        root = root or self.s.music_root
        if not root.is_dir():
            raise FileNotFoundError(f"music root not found: {root}")

        files = await asyncio.to_thread(
            lambda: list(iter_audio_files(root, self.s.follow_symlinks, self.s.skip_hidden)))
        self.stats.found = len(files)

        # The separator is part of the prefix. Without it "/volume1/music" also
        # matches "/volume1/musicbox", so a scan of one folder would load the
        # other folder's rows -- and then mark every one of them MISSING,
        # because this pass never visits them.
        prefix = os.path.join(str(root), "")
        existing = {
            row.path: row for row in (await self.session.execute(
                select(Track).where(Track.path.startswith(prefix))
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
            # Checked between batches rather than per file: the batch that is
            # already in flight finishes and commits, so stopping never
            # discards work that was done. A scan of 3801 files stops within a
            # batch of 200, which is seconds.
            if self.should_stop and self.should_stop():
                self.stats.stopped = True
                break
            chunk = files[start:start + batch]
            await asyncio.gather(*(handle(start + i, p) for i, p in enumerate(chunk)))
            await self.session.commit()

        # Anything indexed but not seen this pass has disappeared from disk --
        # but only if the pass actually finished. After a stop, most of the
        # library is legitimately unvisited, and marking it all MISSING would
        # turn a cancelled scan into a data-quality incident.
        if not self.stats.stopped:
            for path, row in existing.items():
                if path not in seen and row.status == TrackStatus.ACTIVE:
                    row.status = TrackStatus.MISSING
                    self.stats.missing += 1
        await self.session.commit()

        if self.progress:
            await self.progress(len(seen), len(files),
                                "stopped" if self.stats.stopped else "done")
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
        row.mb_recording_id = tags.mb_recording_id or row.mb_recording_id
        row.mb_release_id = tags.mb_release_id or row.mb_release_id
        # `or row.apple_id`, like acoustid two lines above. This was an
        # unconditional overwrite, and tags.apple_id is None for virtually
        # every file: it is read from the MP4-only `cnID` atom and nothing
        # here ever writes that atom back. So every rescan of a matched file
        # -- and enrichment rewrites tags, which changes mtime, which is
        # exactly what makes the scanner re-process it -- erased the Apple
        # match Cadenza had just paid an API call to find, and the next
        # "Match library" run went and found it again.
        row.apple_id = tags.apple_id or row.apple_id

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
