"""A job must not die of its own progress report.

Enrichment on the DS1821+ failed at track five, every time, with
`database is locked` -- raised not by the enrichment but by the UPDATE of the
`jobs` row that exists only to say how far it had got. The providers cache
every response through the job's session, so from the first lookup that
session held SQLite's only write lock, across every rate-limited network call,
until the whole run committed; the progress write, made through a second
session, waited out busy_timeout and raised (issue #61).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.base import SessionFactory, init_db
from app.db.models import AuditLog, Job, JobState, Track, TrackStatus
from app.services import job_runner
from app.services.job_runner import handle_enrich, runner

pytestmark = pytest.mark.asyncio


async def test_a_progress_write_that_cannot_get_the_lock_does_not_fail_the_job(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The write raises the way SQLite does after busy_timeout; the call must
    swallow it, say so, and still tell the live feed."""
    class LockedSession:
        async def execute(self, *_a, **_k):
            raise OperationalError("UPDATE jobs SET processed=?", {},
                                   Exception("database is locked"))

    @asynccontextmanager
    async def locked_scope():
        yield LockedSession()

    monkeypatch.setattr(job_runner, "session_scope", locked_scope)
    feed = runner.subscribe()
    try:
        await runner.progress(424242, 5, 300, "01 - Rasmaha.mp3")   # must not raise
    finally:
        runner.unsubscribe(feed)
    event = feed.get_nowait()
    assert event["type"] == "job.progress" and event["processed"] == 5


@dataclass
class _FakeOutcome:
    track_id: int
    path: str
    confidence: float = 0.0
    applied: bool = False
    changed_fields: dict = field(default_factory=dict)
    field_sources: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    artwork: None = None
    lyrics: None = None
    error: str | None = None
    unmatched: bool = False
    reason: str | None = None


async def test_enrichment_commits_after_every_track(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each track's writes must be visible to another connection before the
    next track starts -- which is exactly what releases the write lock.

    The fake service writes one audit row per track through the handler's
    session, and at the start of each call counts, from a *separate* session,
    how many of those rows have been committed. Without per-track commits the
    count stays at zero for the whole run."""
    await init_db()
    async with SessionFactory() as s:
        tracks = [Track(path=f"/music/probe-{i}.mp3", filename=f"probe-{i}.mp3", ext=".mp3",
                        status=TrackStatus.ACTIVE, size_bytes=1000, tag_completeness=0.1)
                  for i in range(3)]
        s.add_all(tracks)
        job = Job(kind="enrich", state=JobState.RUNNING)
        s.add(job)
        await s.commit()
        ids = [t.id for t in tracks]
        job_id = job.id

    visible_before_each_call: list[int] = []

    class FakeService:
        def __init__(self, session, **_kw):
            self.session = session

        async def enrich(self, track, **_kw):
            async with SessionFactory() as other:
                visible_before_each_call.append((await other.execute(
                    select(func.count(AuditLog.id)).where(AuditLog.action == "enrich-probe")
                )).scalar())
            self.session.add(AuditLog(action="enrich-probe", level="info", track_id=track.id))
            return _FakeOutcome(track_id=track.id, path=track.path)

        async def aclose(self):
            return None

    monkeypatch.setattr(job_runner, "EnrichmentService", FakeService)
    out = await handle_enrich(job_id, {"track_ids": ids, "limit": 10}, True, runner)

    assert out["processed"] == 3
    assert visible_before_each_call == [0, 1, 2], \
        f"rows committed before each track: {visible_before_each_call}"

    async with SessionFactory() as s:
        total = (await s.execute(select(func.count(AuditLog.id))
                                 .where(AuditLog.action == "enrich-probe"))).scalar()
    assert total == 3
    await asyncio.sleep(0)
