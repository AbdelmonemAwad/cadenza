"""The number an earlier organize run threw away comes back from the audit log.

Before 2.11.2 a track with no track-number tag was renamed to `00 - Title`,
and the number its old name carried went with the old name -- 591 files on
one library. Every move wrote an audit row with its source path, and the
earliest one for a track still starts with the number (#82).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.organizer import Organizer
from app.db.base import SessionFactory, init_db
from app.db.models import AuditLog, Track, TrackStatus

pytestmark = pytest.mark.asyncio


def _track(track_id: int, path: str, **kw) -> Track:
    base = {
        "id": track_id, "path": path, "filename": Path(path).name, "ext": Path(path).suffix,
        "size_bytes": 1000, "status": TrackStatus.ACTIVE,
        "title": "A Title", "artist": "An Artist", "albumartist": "An Artist",
        "album": "An Album", "year": 2001, "track_no": None, "disc_no": 1,
    }
    base.update(kw)
    return Track(**base)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "music_root", root)
    return root


async def test_the_number_an_earlier_run_dropped_comes_back(library) -> None:
    await init_db()
    async with SessionFactory() as s:
        renamed = _track(910001, str(library / "2Pac" / "2005 - All Eyez on Me"
                                / "00 - Can't C Me.flac"))
        never_had_one = _track(910002, str(library / "X" / "2001 - An Album"
                                      / "00 - Untitled.flac"))
        no_history = _track(910003, str(library / "Y" / "2001 - An Album" / "00 - Alone.flac"))
        s.add_all([renamed, never_had_one, no_history])
        s.add(AuditLog(action="organize", level="info", track_id=910001, job_id=131,
                       src_path="/music/all/2Pac/All Eyez on Me/15 - Can't C Me.flac",
                       dst_path=renamed.path, reversible=True))
        # A later move of the same track must not win over the earliest name.
        s.add(AuditLog(action="organize", level="info", track_id=910001, job_id=140,
                       src_path=renamed.path, dst_path=renamed.path, reversible=True))
        s.add(AuditLog(action="organize", level="info", track_id=910002, job_id=131,
                       src_path="/music/all/X/Untitled.flac",
                       dst_path=never_had_one.path, reversible=True))
        await s.commit()

        organizer = Organizer(s)
        found = await organizer.recover_numbers([renamed, never_had_one, no_history])
        assert found == {910001: 15}

        assert organizer.render(renamed).name == "15 - A Title.flac"
        assert organizer.render(never_had_one).name == "A Title.flac"
        assert organizer.render(no_history).name == "A Title.flac"


async def test_a_track_with_a_number_is_not_looked_up(library) -> None:
    await init_db()
    async with SessionFactory() as s:
        tagged = _track(910007, str(library / "A" / "2001 - An Album" / "00 - Song.flac"),
                        track_no=9)
        named = _track(910008, str(library / "A" / "2001 - An Album" / "04 - Song.flac"))
        organizer = Organizer(s)
        assert await organizer.recover_numbers([tagged, named]) == {}
        assert organizer.render(tagged).name == "09 - A Title.flac"
        assert organizer.render(named).name == "04 - A Title.flac"
