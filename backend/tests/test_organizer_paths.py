"""Organising must not churn a library that is already tidy, or truncate names.

Both defects here were silent: the files were moved successfully, the job
reported success, and only the library on disk showed what had happened.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.organizer import Organizer
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
    """A music root the organizer is allowed to plan inside."""
    root = tmp_path / "music"
    root.mkdir()
    settings = get_settings()
    monkeypatch.setattr(settings, "music_root", root)
    return root


def test_a_title_containing_a_period_keeps_all_of_it(library, monkeypatch) -> None:
    """`Path.with_suffix` replaces from the LAST dot, not the extension.

    So "02 - Mr. Brightside" was written as "02 - Mr.flac", "P.Y.T. (Pretty
    Young Thing)" as "P.Y.T.flac", and "Vol. 2 Intro" as "Vol.flac". The tags
    were intact; the filename was destroyed.
    """
    organizer = Organizer(None)
    for title, expected in (
        ("Mr. Brightside", "01 - Mr. Brightside.flac"),
        ("P.Y.T. (Pretty Young Thing)", "01 - P.Y.T. (Pretty Young Thing).flac"),
        ("Vol. 2 Intro", "01 - Vol. 2 Intro.flac"),
        ("No Dots Here", "01 - No Dots Here.flac"),
    ):
        rendered = organizer.render(_track("/music/x.flac", title=title, ext=".flac"))
        assert rendered.name == expected, f"{title!r} became {rendered.name!r}"


def test_the_extension_is_not_doubled_when_the_template_supplies_it(library) -> None:
    organizer = Organizer(None)
    rendered = organizer.render(
        _track("/music/x.flac", title="Song", ext=".flac"),
        template="{albumartist}/{title}.{ext}")
    assert rendered.name == "Song.flac", f"got {rendered.name!r}"


def test_a_file_already_in_place_is_not_renamed(library) -> None:
    """The churn.

    `_dedupe_target` saw the track's own file sitting at its rendered target,
    treated it as an occupant, and picked "… (2)". Every correctly-placed track
    was renamed on every run; a third run renamed them back. `skipped` reported
    zero and every rename wrote an audit row.
    """
    organizer = Organizer(None)
    track = _track("/music/placeholder.flac", title="Song", ext=".flac")

    target = organizer.render(track)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"audio")

    settled = _track(str(target), title="Song", ext=".flac")
    plans = organizer.plan([settled])

    assert len(plans) == 1
    assert plans[0].changed is False, f"planned a move to {plans[0].dst}"
    assert plans[0].dst == str(target)
    assert "(2)" not in plans[0].dst


def test_running_it_twice_changes_nothing_the_second_time(library) -> None:
    organizer = Organizer(None)
    track = _track("/music/loose.flac", title="Song", ext=".flac")

    first = organizer.plan([track])[0]
    assert first.changed is True, "a loose file should be planned for a move"

    # Put the file where the first pass would have put it, then re-plan.
    destination = Path(first.dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"audio")

    second = organizer.plan([_track(first.dst, title="Song", ext=".flac")])[0]
    assert second.changed is False, f"a second run moved it to {second.dst}"


def test_a_genuine_collision_still_gets_a_free_name(library) -> None:
    """The dedupe must still work for two different tracks with one target."""
    organizer = Organizer(None)
    a = _track("/music/a.flac", title="Same", ext=".flac")
    b = _track("/music/b.flac", title="Same", ext=".flac")

    plans = organizer.plan([a, b])
    assert plans[0].dst != plans[1].dst, "two tracks were planned onto one path"
    assert "(2)" in plans[1].dst
