"""Library organisation: render a target path from a template, then move safely."""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import string
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AuditLog, Track

log = logging.getLogger(__name__)

# Characters that are legal on Btrfs/ext4 but break over SMB/AFP, so we reject
# the whole Windows-hostile set regardless of the underlying filesystem.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING = re.compile(r"[ .]+$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


@dataclass(slots=True)
class MovePlan:
    track_id: int
    src: str
    dst: str
    changed: bool
    reason: str | None = None


class _SafeDict(dict):
    """Keeps a template with an unknown field from blowing up mid-scan."""

    def __missing__(self, key: str) -> str:
        return "Unknown"


def sanitize(component: str, max_len: int | None = None) -> str:
    s = get_settings()
    max_len = max_len or s.max_path_component
    out = _ILLEGAL.sub(s.replace_unsafe_with, str(component)).strip()
    out = _TRAILING.sub("", out)
    if out.lower() in _RESERVED:
        out = f"_{out}"
    if len(out) > max_len:
        out = out[:max_len].rstrip(" ._-")
    return out or "Unknown"


class Organizer:
    def __init__(self, session: AsyncSession) -> None:
        self.s = get_settings()
        self.session = session

    # ---------------- Path rendering ----------------

    def render(self, track: Track, template: str | None = None) -> Path:
        s = self.s
        if template is None:
            if not track.album:
                template = s.single_template
            elif self._is_compilation(track):
                template = s.compilation_template
            else:
                template = s.path_template

        fields = _SafeDict(
            artist=sanitize(track.artist or track.albumartist or "Unknown Artist"),
            albumartist=sanitize(track.albumartist or track.artist or "Unknown Artist"),
            album=sanitize(track.album or "Unknown Album"),
            title=sanitize(track.title or Path(track.path).stem),
            year=track.year or "0000",
            genre=sanitize(track.genre or "Unknown"),
            track=track.track_no or 0,
            disc=track.disc_no or 1,
            ext=track.ext.lstrip("."),
            codec=track.codec or "unknown",
        )
        try:
            rendered = string.Formatter().vformat(template, (), fields)
        except (ValueError, KeyError) as exc:
            log.warning("invalid path template %r: %s", template, exc)
            rendered = f"{fields['albumartist']}/{fields['album']}/{fields['title']}"

        # Sanitise each component separately so the '/' separators survive.
        parts = [sanitize(p) for p in rendered.split("/") if p.strip()]
        return self.s.music_root / Path(*parts).with_suffix(track.ext)

    @staticmethod
    def _is_compilation(track: Track) -> bool:
        albumartist = (track.albumartist or "").strip().lower()
        return albumartist in ("various artists", "various", "va")

    # ---------------- Planning and execution ----------------

    def plan(self, tracks: list[Track], template: str | None = None) -> list[MovePlan]:
        plans: list[MovePlan] = []
        claimed: set[str] = set()
        for t in tracks:
            try:
                dst = self.render(t, template)
            except Exception as exc:
                plans.append(MovePlan(t.id, t.path, t.path, False, f"template error: {exc}"))
                continue
            dst = self._dedupe_target(dst, claimed)
            claimed.add(str(dst).lower())
            plans.append(MovePlan(
                track_id=t.id, src=t.path, dst=str(dst),
                changed=os.path.normcase(str(dst)) != os.path.normcase(t.path),
            ))
        return plans

    @staticmethod
    def _dedupe_target(dst: Path, claimed: set[str]) -> Path:
        base, i = dst, 2
        while str(dst).lower() in claimed or (dst.exists() and dst.is_file()):
            dst = base.with_name(f"{base.stem} ({i}){base.suffix}")
            i += 1
            if i > 99:
                break
        return dst

    async def apply(self, plans: list[MovePlan], *, dry_run: bool = True,
                    job_id: int | None = None,
                    tracks_by_id: dict[int, Track] | None = None) -> dict:
        moved = skipped = failed = 0
        errors: list[str] = []

        for p in plans:
            if not p.changed:
                skipped += 1
                continue
            if dry_run:
                moved += 1
                continue

            src, dst = Path(p.src), Path(p.dst)
            if not src.is_file():
                failed += 1
                errors.append(f"missing: {src}")
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                self._move_sidecars(src, dst)
                moved += 1

                if tracks_by_id and p.track_id in tracks_by_id:
                    tracks_by_id[p.track_id].path = str(dst)
                    tracks_by_id[p.track_id].filename = dst.name

                self.session.add(AuditLog(
                    action="organize", level="info", track_id=p.track_id, job_id=job_id,
                    src_path=p.src, dst_path=p.dst, reversible=True,
                ))
                self._prune_empty(src.parent)
            except OSError as exc:
                failed += 1
                errors.append(f"{src.name}: {exc}")
                log.warning("move failed %s -> %s: %s", src, dst, exc)

        if not dry_run:
            await self.session.flush()
        return {"dry_run": dry_run, "moved": moved, "skipped": skipped,
                "failed": failed, "errors": errors[:50]}

    @staticmethod
    def _move_sidecars(src: Path, dst: Path) -> None:
        """Carry .lrc/.cue/.log companions along, and copy the album cover."""
        for suffix in (".lrc", ".cue", ".log"):
            side = src.with_suffix(suffix)
            if side.is_file():
                try:
                    shutil.move(str(side), str(dst.with_suffix(suffix)))
                except OSError:
                    log.debug("sidecar move failed: %s", side)
        cover_name = get_settings().cover_filename
        cover, target = src.parent / cover_name, dst.parent / cover_name
        if cover.is_file() and not target.exists():
            # A missing cover copy is cosmetic; never fail the move over it.
            with contextlib.suppress(OSError):
                shutil.copy2(str(cover), str(target))

    def _prune_empty(self, folder: Path) -> None:
        """Remove directories the move emptied, stopping at the library root."""
        root = self.s.music_root
        cur = folder
        while cur != root and root in cur.parents:
            try:
                leftovers = [p for p in cur.iterdir()
                             if p.name not in ("@eaDir", ".DS_Store")]
                if leftovers:
                    return
                shutil.rmtree(cur, ignore_errors=True)
            except OSError:
                return
            cur = cur.parent
