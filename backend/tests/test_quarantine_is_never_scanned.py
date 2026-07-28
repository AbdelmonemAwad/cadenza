"""The quarantine must never be indexed as library.

On the native package the quarantine lives *inside* the library —
`start-stop-status` sets `CADENZA_QUARANTINE_ROOT="${CADENZA_MUSIC_ROOT}/.cadenza-quarantine"`
— and the only thing that kept it out of a scan was the leading dot plus
`skip_hidden`. `skip_hidden` is writable from the Settings page.

Turn it off and the chain runs by itself, with no attacker and no mistake
beyond ticking a box:

  1. the scan indexes every quarantined file as an ACTIVE track
  2. `find_exact_file` groups them with their originals by sha256, at
     confidence 1.0
  3. applying that plan quarantines the "loser" — which may be the file that is
     already in quarantine — to a *new* stamped destination
  4. the original `QuarantineItem.quarantine_path` now points at nothing
  5. the cleanup pass reports it as a stale record and deletes the row, which
     was the only thing remembering where the file came from

The file still exists and nothing can say where it belongs. These tests hold
the first step shut, because every step after it follows correctly from a
premise that should never be true.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.core.scanner import iter_audio_files

pytestmark = pytest.mark.asyncio


@pytest.fixture
def nas_layout(tmp_path, monkeypatch):
    """The layout the Synology package actually ships: quarantine inside the
    library, exactly as start-stop-status configures it."""
    music = tmp_path / "music"
    quarantine = music / ".cadenza-quarantine"
    (music / "album").mkdir(parents=True)
    (quarantine / "2026-07-28").mkdir(parents=True)

    (music / "album" / "song.flac").write_bytes(b"audio")
    (quarantine / "2026-07-28" / "duplicate.flac").write_bytes(b"audio")

    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)
    return music, quarantine


async def test_the_quarantine_is_skipped_even_with_hidden_files_shown(
        nas_layout) -> None:
    """`skip_hidden=False` is a supported setting, not a misconfiguration.

    Someone with a `.hidden` folder of music turns it off for a perfectly good
    reason, and their quarantine gets indexed as library.
    """
    music, quarantine = nas_layout
    found = list(iter_audio_files(music, skip_hidden=False))

    assert any(p.name == "song.flac" for p in found), \
        "the library itself was not scanned"
    assert not [p for p in found if quarantine in p.parents], \
        f"the quarantine was indexed: {[str(p) for p in found]}"


async def test_the_quarantine_is_skipped_with_the_default_setting(
        nas_layout) -> None:
    music, quarantine = nas_layout
    found = list(iter_audio_files(music, skip_hidden=True))
    assert not [p for p in found if quarantine in p.parents]


async def test_a_quarantine_outside_the_library_is_still_excluded(
        tmp_path, monkeypatch) -> None:
    """The container layout puts it elsewhere; the rule must not depend on
    which one is in use."""
    music = tmp_path / "music"
    quarantine = tmp_path / "quarantine"
    music.mkdir()
    quarantine.mkdir()
    (music / "song.flac").write_bytes(b"audio")

    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)

    found = list(iter_audio_files(music, skip_hidden=False))
    assert [p.name for p in found] == ["song.flac"]


async def test_a_renamed_quarantine_folder_is_still_excluded(
        tmp_path, monkeypatch) -> None:
    """The exclusion must follow the setting, not a hardcoded folder name.

    A user who points `CADENZA_QUARANTINE_ROOT` at `<library>/_removed` gets no
    leading dot and no help from `skip_hidden` at all.
    """
    music = tmp_path / "music"
    quarantine = music / "_removed"
    quarantine.mkdir(parents=True)
    (music / "keep.flac").write_bytes(b"audio")
    (quarantine / "gone.flac").write_bytes(b"audio")

    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)

    found = list(iter_audio_files(music, skip_hidden=True))
    assert [p.name for p in found] == ["keep.flac"], \
        f"a quarantine with no leading dot was indexed: {[str(p) for p in found]}"


async def test_the_cleanup_pass_leaves_the_quarantine_alone(
        nas_layout) -> None:
    """Cleanup walks the library too, and the quarantine is full of audio it
    must not touch and sidecars it must not tidy."""
    from app.core.cleanup import LibraryCleaner
    from app.db.base import SessionFactory, init_db

    music, quarantine = nas_layout

    # Deliberately the shapes cleanup looks for, in folders where nothing else
    # would save them. An earlier version of this test put the sidecar beside
    # a quarantined audio file, so it passed because `audio_here` was non-empty
    # -- incidentally, not because anything excluded the quarantine.
    (quarantine / "lyrics-only").mkdir()
    (quarantine / "lyrics-only" / "orphan.lrc").write_text("lyrics")
    (quarantine / "empty-one").mkdir()
    (quarantine / "2026-07-28" / "half.part-abc123.txt").write_text("x")

    await init_db()
    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan(
            {"empty_folders", "orphan_sidecars", "leftover_temp"})

    inside = [i.path for i in report.items if str(quarantine) in i.path]
    assert not inside, f"cleanup offered to remove things from quarantine: {inside}"
