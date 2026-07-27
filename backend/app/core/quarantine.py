"""Safe deletion: move to quarantine, restore with one click. Never an rm -f."""
from __future__ import annotations

import contextlib
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.paths import PathEscape, contained
from app.db.models import AuditLog, QuarantineItem, Track, TrackStatus

log = logging.getLogger(__name__)


class QuarantineError(Exception):
    pass


class QuarantineManager:
    def __init__(self, session: AsyncSession) -> None:
        self.s = get_settings()
        self.session = session
        self.root = self.s.quarantine_root

    # ---------------- Move in ----------------

    async def quarantine(self, track: Track, reason: str, *,
                         group_id: int | None = None,
                         job_id: int | None = None) -> QuarantineItem:
        src = Path(track.path)
        if not src.is_file():
            raise QuarantineError(f"file not found: {src}")

        dest = self._dest_for(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            # os.replace fails across filesystems; shutil.move handles both cases.
            shutil.move(str(src), str(dest))
        except OSError as exc:
            raise QuarantineError(f"could not move into quarantine: {exc}") from exc

        # Take the .lrc sidecar along so it is not orphaned.
        sidecar = src.with_suffix(".lrc")
        if sidecar.is_file():
            try:
                shutil.move(str(sidecar), str(dest.with_suffix(".lrc")))
            except OSError:
                log.debug("sidecar move failed for %s", sidecar)

        item = QuarantineItem(
            original_path=str(src), quarantine_path=str(dest),
            size_bytes=track.size_bytes or 0, sha256=track.sha256,
            reason=reason, group_id=group_id,
            purge_after=datetime.now(UTC).replace(tzinfo=None)
            + timedelta(days=self.s.quarantine_retention_days),
        )
        self.session.add(item)
        track.status = TrackStatus.QUARANTINED

        self.session.add(AuditLog(
            action="quarantine", level="warning", track_id=track.id, job_id=job_id,
            src_path=str(src), dst_path=str(dest), reversible=True,
            detail={"reason": reason, "group_id": group_id, "bytes": track.size_bytes},
        ))
        await self.session.flush()
        return item

    def _dest_for(self, src: Path) -> Path:
        """Mirror the original tree inside quarantine so restores are obvious.

        The relative path is computed from RESOLVED paths. `Path.relative_to`
        is purely lexical: given a source containing `..` it returned a
        relative path that climbed back out, so the ValueError fallback never
        fired and the destination landed outside the quarantine root.
        """
        resolved = src.resolve(strict=False)
        music_root = Path(self.s.music_root).resolve(strict=False)
        try:
            rel = resolved.relative_to(music_root)
        except ValueError:
            rel = Path(resolved.name)

        stamp = datetime.now(UTC).strftime("%Y%m%d")
        dest = self.root / stamp / rel
        counter = 1
        while dest.exists():
            dest = dest.with_name(f"{dest.stem}__{counter}{dest.suffix}")
            counter += 1

        # Belt and braces: whatever the input, the result must land inside the
        # quarantine root or nothing is moved at all.
        try:
            return contained(dest, self.root)
        except PathEscape as exc:
            raise QuarantineError(
                f"refusing to quarantine outside {self.root}: {exc}") from exc

    # ---------------- Restore ----------------

    async def restore(self, item_id: int, *, job_id: int | None = None) -> Path:
        item = (await self.session.execute(
            select(QuarantineItem).where(QuarantineItem.id == item_id)
        )).scalar_one_or_none()
        if item is None:
            raise QuarantineError("quarantine item not found")
        if item.restored:
            raise QuarantineError("already restored")

        src, dest = Path(item.quarantine_path), Path(item.original_path)
        if not src.is_file():
            raise QuarantineError(f"quarantined file is missing: {src}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}__restored{dest.suffix}")
        shutil.move(str(src), str(dest))

        sidecar = Path(item.quarantine_path).with_suffix(".lrc")
        if sidecar.is_file():
            # The audio file is already back; a stranded .lrc is not worth
            # failing the restore over.
            with contextlib.suppress(OSError):
                shutil.move(str(sidecar), str(dest.with_suffix(".lrc")))

        item.restored = True
        item.restored_at = datetime.now(UTC).replace(tzinfo=None)

        track = (await self.session.execute(
            select(Track).where(Track.path == item.original_path)
        )).scalar_one_or_none()
        if track:
            track.status = TrackStatus.ACTIVE
            track.path = str(dest)

        self.session.add(AuditLog(
            action="restore", level="info", job_id=job_id,
            src_path=str(src), dst_path=str(dest), reversible=False,
            detail={"quarantine_id": item_id},
        ))
        await self.session.flush()
        return dest

    async def restore_group(self, group_id: int) -> list[Path]:
        items = (await self.session.execute(
            select(QuarantineItem).where(
                QuarantineItem.group_id == group_id,
                QuarantineItem.restored == False,  # noqa: E712
            )
        )).scalars().all()
        return [await self.restore(i.id) for i in items]

    # ---------------- Purge ----------------

    async def purge_expired(self, *, force: bool = False) -> dict[str, int]:
        """Permanently delete expired items. Requires hard_delete_allowed."""
        if not (self.s.hard_delete_allowed or force):
            return {"purged": 0, "skipped": 0, "blocked": 1}

        now = datetime.now(UTC).replace(tzinfo=None)
        items = (await self.session.execute(
            select(QuarantineItem).where(
                QuarantineItem.restored == False,  # noqa: E712
                QuarantineItem.purge_after <= now,
            )
        )).scalars().all()

        purged = skipped = 0
        for item in items:
            p = Path(item.quarantine_path)
            try:
                if p.is_file():
                    p.unlink()
                purged += 1
                self.session.add(AuditLog(
                    action="purge", level="warning", src_path=str(p), reversible=False,
                    detail={"quarantine_id": item.id, "bytes": item.size_bytes},
                ))
                await self.session.delete(item)
            except OSError as exc:
                skipped += 1
                log.warning("purge failed %s: %s", p, exc)
        await self.session.flush()
        return {"purged": purged, "skipped": skipped}

    async def stats(self) -> dict:
        rows = (await self.session.execute(
            select(QuarantineItem).where(QuarantineItem.restored == False)  # noqa: E712
        )).scalars().all()
        return {
            "items": len(rows),
            "bytes": sum(r.size_bytes for r in rows),
            "oldest": min((r.moved_at for r in rows), default=None),
            "retention_days": self.s.quarantine_retention_days,
            "hard_delete_allowed": self.s.hard_delete_allowed,
        }
