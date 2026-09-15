"""A conversion batch reports each file as it finishes, and stops when asked.

A batch over a whole format runs for hours -- 1,610 FLAC files took four --
and `Transcoder.batch()` used to return only when the last one was done. The
Jobs page read 0/1610 from the first minute to the last and after, and the
Stop button could not stop it. The run was taken, twice, for one that had
done nothing while every file had been written (#81).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.transcode import Transcoder, TranscodeResult

pytestmark = pytest.mark.asyncio


def _fake_transcode(calls: list[str]):
    def transcode(self, src: Path, preset: str, **_kw) -> TranscodeResult:
        calls.append(src.name)
        return TranscodeResult(str(src), str(src.with_suffix(".mp3")), True, preset,
                               src_bytes=100, dst_bytes=40)
    return transcode


async def test_every_finished_file_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(Transcoder, "transcode", _fake_transcode(calls))
    items = [(Path(f"/music/{i:02d} - song.flac"), "mp3_320") for i in range(1, 6)]
    seen: list[tuple[int, int, str]] = []

    async def progress(done: int, total: int, name: str) -> None:
        seen.append((done, total, name))

    results = await Transcoder().batch(items, progress=progress, concurrency=2)

    assert [r.ok for r in results] == [True] * 5
    assert [r.src for r in results] == [str(p) for p, _ in items], "order must be the caller's"
    assert [d for d, _, _ in seen] == [1, 2, 3, 4, 5], "one report per finished file, counting up"
    assert {t for _, t, _ in seen} == {5}
    assert sorted(n for _, _, n in seen) == sorted(p.name for p, _ in items)


async def test_a_stopped_batch_reports_the_files_it_did_not_attempt(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def slow_transcode(self, src: Path, preset: str, **_kw) -> TranscodeResult:
        calls.append(src.name)
        return TranscodeResult(str(src), str(src.with_suffix(".mp3")), True, preset,
                               src_bytes=100, dst_bytes=40)
    monkeypatch.setattr(Transcoder, "transcode", slow_transcode)

    items = [(Path(f"/music/{i:02d} - song.flac"), "mp3_320") for i in range(1, 7)]
    stop = False

    async def progress(done: int, total: int, name: str) -> None:
        nonlocal stop
        if done == 2:
            stop = True          # the Stop button, pressed after two files
        await asyncio.sleep(0)

    results = await Transcoder().batch(items, progress=progress,
                                       should_stop=lambda: stop, concurrency=1)

    attempted = [r for r in results if r.ok]
    skipped = [r for r in results if not r.ok and r.skipped_reason]
    assert len(results) == 6
    assert len(attempted) >= 2 and len(skipped) >= 1, (attempted, skipped)
    assert len(attempted) + len(skipped) == 6, "nothing is counted as failed"
    assert all("stopped" in r.skipped_reason for r in skipped)
    assert all(r.error is None for r in skipped)
    assert len(calls) == len(attempted), "a skipped file must never reach ffmpeg"


async def test_without_callbacks_the_batch_behaves_as_before(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(Transcoder, "transcode", _fake_transcode(calls))
    items = [(Path("/music/a.flac"), "mp3_320"), (Path("/music/b.flac"), "mp3_320")]
    results = await Transcoder().batch(items, concurrency=2)
    assert [r.src for r in results] == ["/music/a.flac", "/music/b.flac"] or \
        [r.src for r in results] == [str(Path("/music/a.flac")), str(Path("/music/b.flac"))]
    assert all(r.ok for r in results)
