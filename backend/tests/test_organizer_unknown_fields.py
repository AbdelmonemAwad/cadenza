"""A field the track has no value for must not become a placeholder in its name.

The first organize preview on a real library planned
`15 - Can't C Me.flac -> 00 - Can't C Me.flac`: the number lived in the
filename and not in the tags, and `{track:02d}` rendered its default. A track
with no year got a `0000 - Album` folder the same way. The run would have
thrown real information away and written a placeholder in its place.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.organizer import Organizer, number_from_filename
from app.db.models import Track, TrackStatus


def _track(path: str, **kw) -> Track:
    base = {
        "id": 1, "path": path, "filename": Path(path).name, "ext": Path(path).suffix,
        "size_bytes": 1000, "status": TrackStatus.ACTIVE,
        "title": "A Title", "artist": "An Artist", "albumartist": "An Artist",
        "album": "An Album", "year": 2001, "track_no": 1, "disc_no": 1,
    }
    base.update(kw)
    return Track(**base)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "music_root", root)
    return root


def test_a_number_in_the_filename_survives_a_missing_tag(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(_track("/music/all/15 - Can't C Me.flac", track_no=None))
    assert rendered.name == "15 - A Title.flac", rendered.name


def test_no_number_anywhere_means_no_number_in_the_name(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(_track("/music/all/Can't C Me.flac", track_no=None))
    assert rendered.name == "A Title.flac", f"a placeholder was written: {rendered.name!r}"


def test_a_missing_year_leaves_the_album_folder_without_one(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(_track("/music/x.flac", year=None))
    assert rendered.parent.name == "An Album", rendered.parent.name
    assert "0000" not in str(rendered)


def test_a_bracketed_year_disappears_with_its_brackets(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(_track("/music/x.flac", year=None),
                                template="{albumartist}/{album} ({year})/{track:02d} - {title}")
    assert rendered.parent.name == "An Album", rendered.parent.name


def test_a_year_at_the_start_of_a_filename_is_not_a_track_number(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(_track("/music/1999 - Song.flac", track_no=None))
    assert rendered.name == "A Title.flac", rendered.name


@pytest.mark.parametrize("stem, expected", [
    ("15 - Can't C Me", 15),
    ("01. Song", 1),
    ("07_Song", 7),
    ("1-05 Song", 5),
    ("2Pac - Song", None),
    ("1999 - Song", None),
    ("Song", None),
    ("15", None),
])
def test_number_from_filename(stem: str, expected: int | None) -> None:
    assert number_from_filename(f"/music/{stem}.flac") == expected
