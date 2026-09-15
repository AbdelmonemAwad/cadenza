"""Quarantining duplicates must end on the number it moved.

A job that quarantined all 838 members of the flagged groups, freed 9.7 GB
and failed on none showed as `done 820/838`: progress was written every
twenty files and never for the last ones (#84). After a conversion that had
written 1,610 files while reading 0/1610, a finished job that reads short is
read as one that did not finish.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.db.base import SessionFactory, init_db
from app.db.models import DupAction, DuplicateGroup, DuplicateMember, Track, TrackStatus
from app.services.job_runner import handle_dedup_apply, runner

pytestmark = pytest.mark.asyncio

BASE_ID = 930_000          # rows no other test in the shared database uses
N = 23                     # not a multiple of the every-twenty report


@pytest.fixture
def library(tmp_path, monkeypatch):
    settings = get_settings()
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", tmp_path / "quarantine")
    return music


async def test_the_last_file_is_reported(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await init_db()
    async with SessionFactory() as s:
        keeper = library / "keep.mp3"
        keeper.write_bytes(b"\x00" * 100)
        tracks = [Track(id=BASE_ID, path=str(keeper), filename=keeper.name, ext=".mp3",
                        status=TrackStatus.ACTIVE, size_bytes=100)]
        for i in range(1, N + 1):
            f = library / f"copy-{i:02d}.mp3"
            f.write_bytes(b"\x00" * 100)
            tracks.append(Track(id=BASE_ID + i, path=str(f), filename=f.name, ext=".mp3",
                                status=TrackStatus.ACTIVE, size_bytes=100))
        s.add_all(tracks)
        group = DuplicateGroup(kind="exact_audio", signature="sig-progress", confidence=1.0,
                               member_count=N + 1, reclaimable_bytes=100 * N)
        s.add(group)
        await s.flush()
        s.add(DuplicateMember(group_id=group.id, track_id=BASE_ID, score=0.9,
                              proposed_action=DupAction.KEEP))
        for i in range(1, N + 1):
            s.add(DuplicateMember(group_id=group.id, track_id=BASE_ID + i, score=0.4,
                                  proposed_action=DupAction.QUARANTINE))
        await s.commit()
        group_id = group.id

    reports: list[tuple[int, int, str]] = []

    async def record(job_id: int, done: int, total: int, message: str | None = None) -> None:
        reports.append((done, total, message or ""))

    monkeypatch.setattr(runner, "progress", record)
    out = await handle_dedup_apply(0, {"group_ids": [group_id]}, False, runner)

    assert (out["quarantined"], out["failed"]) == (N, 0), out
    assert (20, N) in {(d, t) for d, t, _ in reports}, "the every-twenty report still happens"
    assert reports[-1][:2] == (N, N), f"the job ended reading {reports[-1][0]}/{N}"
    assert not (library / "copy-23.mp3").exists(), "the last file was moved"
