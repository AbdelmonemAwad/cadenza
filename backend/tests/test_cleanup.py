"""Tidying must never remove music.

A "tidy up" button is exactly the sort of feature that deletes something
irreplaceable by accident, so most of this file is about what it refuses. The
audio check runs twice — once when the list is drawn up and again at the moment
of the change — and both are tested, because the scan and the apply are
separate requests and a file can appear between them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.cleanup import CATEGORIES, CleanupItem, CleanupReport, LibraryCleaner
from app.db.base import SessionFactory, init_db
from app.db.models import Track, TrackStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    quarantine = tmp_path / "quarantine"
    root.mkdir()
    quarantine.mkdir()
    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", root)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)
    return root


async def test_an_audio_file_is_never_a_candidate(library) -> None:
    """The whole point of the feature, asserted first.

    A .flac carrying a temp marker in its name still must not be offered: the
    marker is a heuristic, the extension is not.
    """
    (library / "album").mkdir()
    (library / "album" / "song.part-abc123.flac").write_bytes(b"audio")
    (library / "album" / "real.flac").write_bytes(b"audio")

    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan({"leftover_temp"})

    assert not [i for i in report.items if i.path.endswith(".flac")], \
        f"an audio file was offered for removal: {[i.path for i in report.items]}"
    assert report.refused, "the refusal was not recorded"


async def test_an_audio_file_is_refused_again_at_apply_time(library) -> None:
    """The scan and the apply are separate requests.

    A file can appear between them — a conversion finishing, a copy landing
    over SMB — so a list drawn up when the path was safe must not be trusted
    when it is acted on.
    """
    (library / "album").mkdir()
    sneaked = library / "album" / "appeared.flac"
    sneaked.write_bytes(b"audio")

    report = CleanupReport(items=[
        CleanupItem("leftover_temp", str(sneaked), 5, "hand-built")])

    async with SessionFactory() as s:
        out = await LibraryCleaner(s).apply(report, dry_run=False)

    assert sneaked.is_file(), "an audio file was removed at apply time"
    assert out["failed"] == 1
    assert any("audio" in e for e in out["errors"])


async def test_a_path_outside_the_library_is_refused(library, tmp_path) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("not yours")
    report = CleanupReport(items=[
        CleanupItem("leftover_temp", str(outside), 9, "hand-built")])

    async with SessionFactory() as s:
        out = await LibraryCleaner(s).apply(report, dry_run=False)

    assert outside.is_file(), "a file outside the library was removed"
    assert out["failed"] == 1


# ------------------------------ what it finds ------------------------------

async def test_empty_folders_are_found_bottom_up(library) -> None:
    """A folder holding only empty folders is itself empty once they go.

    A top-down walk reports only the leaves, so the parent survives and the
    user has to run the job repeatedly to make progress.
    """
    (library / "outer" / "inner").mkdir(parents=True)

    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan({"empty_folders"})

    found = {Path(i.path).name for i in report.items}
    assert "inner" in found
    assert "outer" in found, "the parent was not recognised as empty"


async def test_a_folder_holding_music_is_not_empty(library) -> None:
    (library / "album").mkdir()
    (library / "album" / "song.flac").write_bytes(b"audio")

    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan({"empty_folders"})

    assert not report.items


async def test_sidecars_are_only_orphans_when_no_audio_is_left(library) -> None:
    """A .lrc beside its track is the point of a .lrc."""
    (library / "with-music").mkdir()
    (library / "with-music" / "song.flac").write_bytes(b"audio")
    (library / "with-music" / "song.lrc").write_text("lyrics")

    (library / "abandoned").mkdir()
    (library / "abandoned" / "song.lrc").write_text("lyrics")
    (library / "abandoned" / "cover.jpg").write_bytes(b"jpeg")

    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan({"orphan_sidecars"})

    paths = {i.path for i in report.items}
    assert str(library / "abandoned" / "song.lrc") in paths
    assert str(library / "abandoned" / "cover.jpg") in paths
    assert str(library / "with-music" / "song.lrc") not in paths, \
        "the lyrics of a track that is still there were offered for removal"


async def test_synology_caches_are_found_but_not_walked_into(library) -> None:
    eadir = library / "album" / "@eaDir"
    eadir.mkdir(parents=True)
    (eadir / "thumb.jpg").write_bytes(b"jpeg")
    (eadir / "SYNOPHOTO_THUMB.jpg").write_bytes(b"jpeg")

    async with SessionFactory() as s:
        report = await LibraryCleaner(s).scan({"nas_metadata", "orphan_sidecars"})

    assert [i for i in report.items if i.category == "nas_metadata"]
    inside = [i for i in report.items if "@eaDir" in i.path
              and i.category != "nas_metadata"]
    assert not inside, "files inside a cache directory were listed individually"


async def test_a_cache_directory_containing_audio_is_left_alone(library) -> None:
    """A surprise worth stopping for, not resolving in favour of tidiness."""
    from app.core.cleanup import _remove_tree

    eadir = library / "@eaDir"
    eadir.mkdir()
    (eadir / "somehow.flac").write_bytes(b"audio")

    with pytest.raises(OSError, match="contains audio"):
        _remove_tree(eadir)
    assert (eadir / "somehow.flac").is_file()


async def test_rows_for_files_that_are_gone_are_offered(library) -> None:
    await init_db()
    async with SessionFactory() as s:
        s.add(Track(path=str(library / "vanished.flac"), filename="vanished.flac",
                    ext=".flac", status=TrackStatus.MISSING, size_bytes=100))
        await s.commit()

        report = await LibraryCleaner(s).scan({"missing_tracks"})

    rows = [i for i in report.items if i.category == "missing_tracks"]
    assert rows and rows[0].database_only is True


async def test_a_missing_row_whose_file_came_back_is_left_alone(library) -> None:
    """The file returned. Not this job's business either way — but certainly
    not its business to delete the row."""
    await init_db()
    back = library / "returned.flac"
    back.write_bytes(b"audio")

    async with SessionFactory() as s:
        s.add(Track(path=str(back), filename="returned.flac", ext=".flac",
                    status=TrackStatus.MISSING, size_bytes=100))
        await s.commit()

        report = await LibraryCleaner(s).scan({"missing_tracks"})

    assert not [i for i in report.items if i.path == str(back)]


# -------------------------------- behaviour --------------------------------

async def test_a_dry_run_changes_nothing(library) -> None:
    (library / "empty").mkdir()
    (library / "abandoned").mkdir()
    (library / "abandoned" / "song.lrc").write_text("lyrics")

    async with SessionFactory() as s:
        cleaner = LibraryCleaner(s)
        report = await cleaner.scan({"empty_folders", "orphan_sidecars"})
        out = await cleaner.apply(report, dry_run=True)

    assert out["dry_run"] is True
    assert (library / "empty").is_dir(), "a dry run removed a folder"
    assert (library / "abandoned" / "song.lrc").is_file(), \
        "a dry run removed a file"


async def test_an_unknown_category_is_refused(library) -> None:
    async with SessionFactory() as s:
        with pytest.raises(ValueError, match="unknown cleanup categories"):
            await LibraryCleaner(s).scan({"delete_everything"})


async def test_every_category_has_a_description() -> None:
    """The interface reads these; a category without one renders blank."""
    from app.api.v1.cleanup import DESCRIPTIONS

    assert set(DESCRIPTIONS) == set(CATEGORIES)
    for key, entry in DESCRIPTIONS.items():
        assert entry["what"] and entry["detail"], f"{key} has no description"
