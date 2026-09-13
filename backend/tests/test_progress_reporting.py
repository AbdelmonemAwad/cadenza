"""A job's progress must end where the job did.

Duplicate analysis on a real library finished `done` with the Jobs page
showing `20000/4`. The engine reported on two scales -- stage numbers out of
four, then acoustic block counts out of twenty thousand -- and the handler
scheduled each report as its own coroutine from a worker thread and awaited
none of them. Two were in flight at once; each had loaded the row, each
changed the attribute it cared about, and SQLAlchemy wrote only what changed:
one landed `processed`, the other `total`, and nothing wrote the pair again.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.core.dedup import DeduplicationEngine
from app.db.base import SessionFactory, init_db
from app.db.models import Job, JobState
from app.services.job_runner import runner
from tests.test_dedup_engine import seeded_session  # noqa: F401  -- fixture

pytestmark = pytest.mark.asyncio


async def _job() -> int:
    await init_db()
    async with SessionFactory() as s:
        job = Job(kind="progress-probe", state=JobState.RUNNING)
        s.add(job)
        await s.commit()
        return job.id


async def _pair(job_id: int) -> tuple[int, int]:
    async with SessionFactory() as s:
        job = await s.get(Job, job_id, populate_existing=True)
        return job.processed, job.total


async def test_two_reports_in_flight_never_mix_their_columns() -> None:
    """The exact shape of the defect: a row at 19800/20000, then a block
    report that changes only `processed` racing a stage report that changes
    both. Either whole pair is a correct outcome; one column from each is
    what the page showed for weeks."""
    job_id = await _job()
    for _ in range(25):
        await runner.progress(job_id, 19800, 20000)
        await asyncio.gather(runner.progress(job_id, 20000, 20000),
                             runner.progress(job_id, 3, 4))
        pair = await _pair(job_id)
        assert pair in {(20000, 20000), (3, 4)}, f"columns from two reports: {pair}"


async def test_reports_from_a_worker_thread_land_in_order_and_the_last_wins() -> None:
    # Imported here so the two tests above still run -- and fail for the
    # right reason -- on a tree that does not have the relay yet.
    from app.services.job_runner import ProgressRelay

    job_id = await _job()
    relay = ProgressRelay(runner, job_id, asyncio.get_running_loop())

    def worker() -> None:
        for i in range(1, 301):
            relay.report(f"step {i}", i, 300)

    thread = threading.Thread(target=worker)
    thread.start()
    await asyncio.to_thread(thread.join)
    await relay.close()

    async with SessionFactory() as s:
        job = await s.get(Job, job_id, populate_existing=True)
    assert (job.processed, job.total) == (300, 300), \
        f"the last report did not win: {job.processed}/{job.total}"
    assert job.message == "step 300"


async def test_analysis_reports_on_one_scale_that_only_climbs(seeded_session) -> None:  # noqa: F811
    """Whatever stage speaks, the numbers are out of 100 and never go back."""
    seen: list[tuple[str, int, int]] = []
    engine = DeduplicationEngine(seeded_session, lambda stage, done, total:
                                 seen.append((stage, done, total)))
    await engine.analyze(use_acoustic=True)

    assert seen, "the engine reported nothing"
    assert {total for _, _, total in seen} == {100}, \
        f"more than one scale: {sorted({t for _, _, t in seen})}"
    dones = [done for _, done, _ in seen]
    assert dones == sorted(dones), f"progress went backwards: {dones}"
    assert dones[0] == 0 and dones[-1] == 90
    assert any(stage.startswith("acoustic") for stage, _, _ in seen)
