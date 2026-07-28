"""What a scan must and must not do.

Two of these cover damage a stop button can cause if it is bolted on without
thought: a scan that is halted has legitimately not visited most of the
library, and a scan of one folder must not reason about another one's rows.
Both would present as data loss to the user -- tracks turning MISSING on their
own -- with no error anywhere.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.scanner import LibraryScanner, iter_audio_files
from app.db.base import SessionFactory, init_db
from app.db.models import Track, TrackStatus

pytestmark = pytest.mark.asyncio


def _write_library(root: Path, count: int, prefix: str = "song") -> list[Path]:
    """Files with a real audio extension. Content is irrelevant here: probing
    fails, they are marked CORRUPT, and every path this exercises still runs."""
    root.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(count):
        p = root / f"{prefix}{i:03d}.mp3"
        p.write_bytes(b"\x00" * 40_000)
        made.append(p)
    return made


async def test_walk_finds_audio_and_skips_synology_noise(tmp_path: Path) -> None:
    _write_library(tmp_path / "album", 3)
    (tmp_path / "@eaDir").mkdir()
    (tmp_path / "@eaDir" / "thumb.mp3").write_bytes(b"x")
    (tmp_path / "album" / ".hidden.mp3").write_bytes(b"x")
    (tmp_path / "album" / "notes.txt").write_text("not audio")

    found = {p.name for p in iter_audio_files(tmp_path)}
    assert found == {"song000.mp3", "song001.mp3", "song002.mp3"}


async def test_stopping_does_not_mark_the_rest_of_the_library_missing(
        tmp_path: Path) -> None:
    """The failure this exists to prevent.

    A stopped scan has not visited most of the library. If the missing sweep
    still ran, every unvisited track would be marked MISSING -- the user asks
    a long scan to stop and watches their library appear to empty itself.
    """
    await init_db()
    root = tmp_path / "music"
    _write_library(root, 500)

    async with SessionFactory() as s:
        # A row that this scan will never reach, in ACTIVE state.
        s.add(Track(path=str(root / "song499.mp3"), filename="song499.mp3",
                    ext=".mp3", status=TrackStatus.ACTIVE, size_bytes=40_000))
        await s.commit()

        # Stop immediately: not one batch is processed.
        stats = await LibraryScanner(s, should_stop=lambda: True).scan(root)
        assert stats.stopped is True
        assert stats.missing == 0, "a stopped scan must not declare anything missing"

        row = (await s.execute(
            select(Track).where(Track.path == str(root / "song499.mp3")))).scalar_one()
        assert row.status == TrackStatus.ACTIVE


async def test_a_completed_scan_still_marks_vanished_files_missing(
        tmp_path: Path) -> None:
    """The sweep must keep working when the scan is not interrupted."""
    await init_db()
    root = tmp_path / "music2"
    _write_library(root, 2)

    async with SessionFactory() as s:
        s.add(Track(path=str(root / "deleted.mp3"), filename="deleted.mp3",
                    ext=".mp3", status=TrackStatus.ACTIVE, size_bytes=40_000))
        await s.commit()

        stats = await LibraryScanner(s).scan(root)
        assert stats.stopped is False
        assert stats.missing == 1

        row = (await s.execute(
            select(Track).where(Track.path == str(root / "deleted.mp3")))).scalar_one()
        assert row.status == TrackStatus.MISSING


async def test_a_sibling_folder_with_a_shared_prefix_is_left_alone(
        tmp_path: Path) -> None:
    """`/music` must not match `/musicbox`.

    The rows were selected with a bare `startswith(root)`, so scanning one
    folder loaded the other's tracks as `existing` -- and then marked them all
    MISSING, because this pass never visits them.
    """
    await init_db()
    inside = tmp_path / "music"
    beside = tmp_path / "musicbox"
    _write_library(inside, 1)
    _write_library(beside, 1, prefix="other")

    async with SessionFactory() as s:
        s.add(Track(path=str(beside / "other000.mp3"), filename="other000.mp3",
                    ext=".mp3", status=TrackStatus.ACTIVE, size_bytes=40_000))
        await s.commit()

        stats = await LibraryScanner(s).scan(inside)
        assert stats.missing == 0, "the neighbouring folder's track was swept up"

        row = (await s.execute(
            select(Track).where(Track.path == str(beside / "other000.mp3")))).scalar_one()
        assert row.status == TrackStatus.ACTIVE


async def test_the_expensive_figures_are_off_by_default(tmp_path: Path) -> None:
    """94% of a scan's cost, and nothing needs it to browse a library.

    Guarded because the default is the whole point: a 3801-file library took
    forty minutes with these on and about two without.
    """
    import inspect

    sig = inspect.signature(LibraryScanner.scan)
    assert sig.parameters["compute_fingerprints"].default is False
    assert sig.parameters["compute_audio_md5"].default is False
