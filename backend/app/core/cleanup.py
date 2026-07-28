"""Tidying the library: the things that are left behind, not the music.

A library that has been ripped, moved, converted and re-tagged for years
accumulates litter — empty folders where an album used to be, a `.lrc` whose
audio was deleted, Synology's `@eaDir` thumbnail caches, a `.part` file from a
conversion that was interrupted, database rows for files that are no longer
there.

None of it is dangerous and all of it is invisible, which is why it survives.

The one rule
------------
**This never touches an audio file.** Not to move it, not to delete it, not to
quarantine it. Every candidate is checked against `AUDIO_EXTENSIONS` before it
is offered and again before it is acted on, and the second check is not
redundant: the scan and the apply are separate requests, and a file can appear
between them.

Removing a file the user cannot get back is the one thing this whole project
promises not to do, and a "tidy up" button is exactly the sort of thing that
does it by accident. So:

  * audio files are refused outright, in both passes
  * anything that is not obviously worthless goes to quarantine, not to /dev/null
  * empty directories are removed rather than quarantined, because a directory
    with nothing in it carries nothing to lose -- and it is recreated the
    moment anything is written there
  * every category is opt-in; nothing runs because it was the default
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AUDIO_EXTENSIONS, get_settings
from app.core.paths import PathEscape, contained
from app.db.models import QuarantineItem, Track, TrackStatus

log = logging.getLogger(__name__)

# Synology writes these throughout a shared folder. They are caches: DSM
# regenerates them, and nothing of the user's lives in them.
NAS_METADATA_DIRS = {"@eaDir", "#recycle", "@tmp", "#snapshot"}

# Left by an interrupted conversion or move. The conversion writes
# `.track.part-a1b2c3.flac` and renames it into place, so anything still
# carrying the marker is the remains of a run that did not finish.
TEMP_MARKERS = (".part-", ".tmp", ".partial")

# Files that only make sense next to an audio file of the same name, or in a
# folder that holds audio at all.
SIDECAR_SUFFIXES = (".lrc", ".cue", ".log", ".m3u", ".m3u8")

CATEGORIES = (
    "empty_folders",
    "orphan_sidecars",
    "nas_metadata",
    "leftover_temp",
    "missing_tracks",
    "stale_quarantine",
)


@dataclass(slots=True)
class CleanupItem:
    category: str
    path: str
    size_bytes: int
    reason: str
    # Database rows have no file to move; they are removed from the index only.
    database_only: bool = False

    def as_dict(self) -> dict:
        return {"category": self.category, "path": self.path,
                "size_bytes": self.size_bytes, "reason": self.reason,
                "database_only": self.database_only}


@dataclass(slots=True)
class CleanupReport:
    items: list[CleanupItem] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def by_category(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for item in self.items:
            entry = out.setdefault(item.category, {"count": 0, "bytes": 0})
            entry["count"] += 1
            entry["bytes"] += item.size_bytes
        return out


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


class LibraryCleaner:
    """Finds and removes litter. Never touches the music."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = get_settings()
        self.session = session
        self.root = Path(self.s.music_root)

    # ------------------------------ finding ------------------------------

    async def scan(self, categories: set[str], *,
                   limit: int = 5000) -> CleanupReport:
        report = CleanupReport()
        unknown = categories - set(CATEGORIES)
        if unknown:
            raise ValueError(f"unknown cleanup categories: {sorted(unknown)}")

        if categories & {"empty_folders", "orphan_sidecars", "nas_metadata",
                         "leftover_temp"}:
            self._walk(categories, report, limit)

        if "missing_tracks" in categories:
            await self._missing_tracks(report, limit)
        if "stale_quarantine" in categories:
            await self._stale_quarantine(report, limit)

        return report

    def _walk(self, categories: set[str], report: CleanupReport,
              limit: int) -> None:
        """One pass over the library, bottom-up.

        Bottom-up matters for `empty_folders`: a folder containing only empty
        folders is itself empty once they go, and a top-down walk would report
        only the leaves. Walking upwards means the parent is examined after its
        children have already been counted as removable.
        """
        want_empty = "empty_folders" in categories
        want_sidecars = "orphan_sidecars" in categories
        want_nas = "nas_metadata" in categories
        want_temp = "leftover_temp" in categories
        cover = self.s.cover_filename.lower()

        # Directories already accounted for, so a parent can tell whether it
        # will be left with nothing.
        removable_dirs: set[str] = set()

        # Never walk into these, whatever they are configured to.
        #
        # On the Synology package the quarantine is *inside* the library, and
        # the data folder can be too -- the install wizard accepts any path. A
        # bare os.walk therefore reaches both, and both are full of exactly the
        # shapes this looks for: quarantine holds orphaned sidecars and the
        # dated folders left behind after a restore, and the data folder holds
        # `logs/*.log` (`.log` is in SIDECAR_SUFFIXES, and that folder has no
        # audio in it) and `cache/artwork/cover.jpg` (which matches the cover
        # branch exactly).
        #
        # So tidying up would have quarantined Cadenza's own logs, and offered
        # to remove the records of files the user had already quarantined.
        off_limits = {
            Path(self.s.quarantine_root).resolve(strict=False),
            Path(self.s.config_dir).resolve(strict=False),
        }

        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            here = Path(dirpath)
            if here == self.root:
                continue
            resolved = here.resolve(strict=False)
            if resolved in off_limits or any(p in off_limits for p in resolved.parents):
                continue

            name = here.name
            if name in NAS_METADATA_DIRS:
                if want_nas and len(report.items) < limit:
                    size = _tree_size(here)
                    report.items.append(CleanupItem(
                        "nas_metadata", str(here), size,
                        f"{name} is a Synology cache; DSM regenerates it"))
                    removable_dirs.add(str(here))
                # Never walk into one looking for other things.
                dirnames[:] = []
                continue
            if any(part in NAS_METADATA_DIRS for part in here.parts):
                continue

            audio_here = [f for f in filenames if is_audio(Path(f))]

            if want_temp and len(report.items) < limit:
                for f in filenames:
                    lowered = f.lower()
                    if any(marker in lowered for marker in TEMP_MARKERS):
                        candidate = here / f
                        if self._refuse_audio(candidate, report):
                            continue
                        report.items.append(CleanupItem(
                            "leftover_temp", str(candidate), _size(candidate),
                            "left by a conversion or move that did not finish"))

            if want_sidecars and not audio_here and len(report.items) < limit:
                # Only when the folder holds no audio at all. A `.lrc` beside
                # its track is the point of a `.lrc`; one in a folder whose
                # music has gone is litter. Judging them individually by
                # matching stems would delete the lyrics of a track that is
                # merely being re-encoded at that moment.
                for f in filenames:
                    lowered = f.lower()
                    if lowered.endswith(SIDECAR_SUFFIXES) or lowered == cover:
                        candidate = here / f
                        if self._refuse_audio(candidate, report):
                            continue
                        report.items.append(CleanupItem(
                            "orphan_sidecars", str(candidate), _size(candidate),
                            "no audio left in this folder"))

            if want_empty and len(report.items) < limit:
                remaining_dirs = [d for d in dirnames
                                  if str(here / d) not in removable_dirs]
                if not filenames and not remaining_dirs:
                    report.items.append(CleanupItem(
                        "empty_folders", str(here), 0, "contains nothing"))
                    removable_dirs.add(str(here))

    def _refuse_audio(self, path: Path, report: CleanupReport) -> bool:
        if is_audio(path):
            report.refused.append(f"{path}: audio files are never removed here")
            log.warning("cleanup refused an audio file: %s", path)
            return True
        return False

    async def _missing_tracks(self, report: CleanupReport, limit: int) -> None:
        rows = (await self.session.execute(
            select(Track).where(Track.status == TrackStatus.MISSING).limit(limit)
        )).scalars().all()
        for row in rows:
            if Path(row.path).is_file():
                # It came back. Not this job's business to fix the status, but
                # certainly not its business to delete the row either.
                continue
            report.items.append(CleanupItem(
                "missing_tracks", row.path, row.size_bytes or 0,
                "indexed but the file is no longer on disk",
                database_only=True))

    async def _stale_quarantine(self, report: CleanupReport, limit: int) -> None:
        rows = (await self.session.execute(
            select(QuarantineItem).where(
                QuarantineItem.restored == False).limit(limit)   # noqa: E712
        )).scalars().all()
        for row in rows:
            if Path(row.quarantine_path).exists():
                continue
            report.items.append(CleanupItem(
                "stale_quarantine", row.quarantine_path, row.size_bytes or 0,
                "quarantine record whose file is gone; it cannot be restored",
                database_only=True))

    # ------------------------------ applying ------------------------------

    async def apply(self, report: CleanupReport, *, dry_run: bool = True,
                    job_id: int | None = None) -> dict:
        """Act on a report. Files go to quarantine; empty folders are removed.

        The audio check runs again here. The scan and the apply are separate
        requests and a file can appear between them -- a conversion finishing,
        a copy landing over SMB -- so the guarantee has to be enforced at the
        moment of the change, not only when the list was drawn up.
        """
        from app.core.quarantine import QuarantineManager

        removed = quarantined = rows_removed = failed = 0
        errors: list[str] = []
        manager = QuarantineManager(self.session)

        for item in report.items:
            path = Path(item.path)

            if item.database_only:
                if not dry_run:
                    await self._forget(item)
                rows_removed += 1
                continue

            if is_audio(path):
                failed += 1
                errors.append(f"{path.name}: audio files are never removed here")
                log.error("cleanup refused an audio file at apply time: %s", path)
                continue

            try:
                contained(path, self.root)
            except PathEscape as exc:
                failed += 1
                errors.append(f"{path.name}: outside the library, refused")
                log.error("cleanup refused a path outside the library: %s (%s)",
                          path, exc)
                continue

            if dry_run:
                removed += 1
                continue

            try:
                if item.category == "empty_folders":
                    # rmdir, never rmtree. If anything has appeared in it since
                    # the scan, this fails and leaves it alone -- which is the
                    # correct outcome and the reason not to use rmtree.
                    path.rmdir()
                    removed += 1
                elif item.category == "nas_metadata":
                    _remove_tree(path)
                    removed += 1
                elif item.category == "leftover_temp":
                    path.unlink(missing_ok=True)
                    removed += 1
                else:
                    # A sidecar might be someone's hand-written lyrics. It goes
                    # to quarantine like any other removal, so it comes back
                    # with one click.
                    await manager.quarantine_file(
                        path, reason=f"cleanup:{item.category}", job_id=job_id)
                    quarantined += 1
            except OSError as exc:
                failed += 1
                errors.append(f"{path.name}: {exc}")
                log.warning("cleanup could not remove %s: %s", path, exc)

        if not dry_run:
            await self.session.commit()

        return {
            "dry_run": dry_run,
            "removed": removed,
            "quarantined": quarantined,
            "rows_removed": rows_removed,
            "failed": failed,
            "refused": report.refused[:50],
            "errors": errors[:50],
            "by_category": report.by_category(),
        }

    async def _forget(self, item: CleanupItem) -> None:
        if item.category == "missing_tracks":
            row = (await self.session.execute(
                select(Track).where(Track.path == item.path)
            )).scalar_one_or_none()
            if row is not None and not Path(row.path).is_file():
                await self.session.delete(row)
        elif item.category == "stale_quarantine":
            row = (await self.session.execute(
                select(QuarantineItem).where(
                    QuarantineItem.quarantine_path == item.path)
            )).scalar_one_or_none()
            if row is not None and not Path(row.quarantine_path).exists():
                await self.session.delete(row)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_size(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            total += _size(Path(dirpath) / name)
    return total


def _remove_tree(root: Path) -> None:
    """Delete a cache directory, refusing if it turns out to hold audio.

    `@eaDir` holds thumbnails and DSM's own index files. If a user has somehow
    put music in one, that is a surprise worth stopping for rather than
    resolving in favour of tidiness.
    """
    for _dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if is_audio(Path(name)):
                raise OSError(f"{root} contains audio ({name}); left alone")

    shutil.rmtree(root)
