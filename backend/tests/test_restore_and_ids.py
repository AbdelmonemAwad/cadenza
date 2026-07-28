"""Two ways the app used to destroy work it had already done.

A partial group restore left files back in the library with rows that still
said "quarantined", and no way to fix it from the interface. And a rescan wiped
the Apple match the user had just paid API calls for.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.quarantine import QuarantineManager
from app.db.base import SessionFactory, init_db
from app.db.models import QuarantineItem

pytestmark = pytest.mark.asyncio


async def test_a_failure_halfway_through_a_group_keeps_what_it_restored(
        tmp_path: Path, monkeypatch) -> None:
    """The stranding.

    `restore_group` used to be a list comprehension inside the caller's single
    transaction. The first failure propagated, the endpoint answered 400, and
    the commit never happened -- but `restore` moves the file before it touches
    the database, so the earlier files were already back in the library with
    rows still saying `restored = false`. Pressing Restore again then answered
    "quarantined file is missing", for ever, because the file had already been
    moved. The row kept counting toward the dashboard totals.
    """
    await init_db()

    music = tmp_path / "music"
    quarantine = tmp_path / "quarantine"
    music.mkdir()
    quarantine.mkdir()

    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)

    good = quarantine / "good.flac"
    good.write_bytes(b"audio")
    missing = quarantine / "gone.flac"          # deliberately never created

    async with SessionFactory() as s:
        s.add_all([
            QuarantineItem(original_path=str(music / "good.flac"),
                           quarantine_path=str(good), size_bytes=5,
                           reason="test", group_id=4242),
            QuarantineItem(original_path=str(music / "gone.flac"),
                           quarantine_path=str(missing), size_bytes=5,
                           reason="test", group_id=4242),
        ])
        await s.commit()

        outcome = await QuarantineManager(s).restore_group(4242)

    assert len(outcome["restored"]) == 1, "the healthy item was not restored"
    assert len(outcome["failed"]) == 1, "the broken item was not reported"

    # The file really is back.
    assert (music / "good.flac").is_file()
    assert not good.exists()

    # And the row says so, which is the half that used to be rolled back.
    async with SessionFactory() as s:
        rows = (await s.execute(
            select(QuarantineItem).where(QuarantineItem.group_id == 4242)
        )).scalars().all()
        by_path = {Path(r.quarantine_path).name: r for r in rows}
        assert by_path["good.flac"].restored is True, \
            "the file was moved but the database was rolled back -- stranded"
        assert by_path["gone.flac"].restored is False


async def test_the_scanner_source_preserves_provider_ids() -> None:
    """`row.apple_id = tags.apple_id` was an unconditional overwrite.

    `tags.apple_id` is read from the MP4-only `cnID` atom and nothing ever
    writes that atom back, so the value Cadenza matched is never in the file to
    be read again. Every rescan of a matched track therefore reset it to NULL --
    and enrichment rewrites tags, which changes mtime, which is exactly what
    makes the scanner re-process a file. The next "Match library" run then
    re-matched the same tracks and re-spent the API calls.

    Asserted against the source rather than by driving a scan: exercising it
    for real needs a probeable audio file, and a test that builds one would be
    testing ffmpeg. This pins the one character that matters, and says so.
    """
    source = Path(__file__).resolve().parents[1] / "app" / "core" / "scanner.py"
    text = source.read_text(encoding="utf-8")
    for field in ("apple_id", "mb_recording_id", "mb_release_id", "acoustid"):
        assert f"row.{field} = tags.{field} or row.{field}" in text, \
            f"row.{field} is assigned without preserving an existing value"
