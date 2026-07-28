"""Two ways the job runner disagreed with the rest of the application.

Both were invisible: the interface showed the right thing and the runner did
something else.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings, save_settings
from app.db.base import SessionFactory, init_db
from app.db.models import Job, JobState
from app.services.job_runner import reconcile_orphaned_jobs, runner

pytestmark = pytest.mark.asyncio


async def test_a_settings_change_reaches_the_runner_without_a_restart() -> None:
    """`self.settings` was captured in __init__, which runs at import.

    `save_settings` rebuilds the cache as a brand-new object, so the runner
    kept the values the process booted with for ever. The visible half was a
    scheduled purge staying in preview mode after the user turned preview off.
    The dangerous half is the other direction: turning preview back ON left
    every scheduled enrich, organize and convert executing for real until the
    package was restarted -- with the interface showing the setting as saved.
    """
    await init_db()
    original = get_settings().dry_run_default
    try:
        save_settings({"dry_run_default": False})
        job_id = await runner.submit("scan", {})
        async with SessionFactory() as s:
            job = await s.get(Job, job_id)
            assert job.dry_run is False, "the runner is still using the old setting"

        save_settings({"dry_run_default": True})
        job_id = await runner.submit("scan", {})
        async with SessionFactory() as s:
            job = await s.get(Job, job_id)
            assert job.dry_run is True, \
                "turning preview back on did not reach the runner -- jobs would " \
                "keep executing for real"
    finally:
        save_settings({"dry_run_default": original})


async def test_jobs_left_behind_by_a_restart_are_closed_out() -> None:
    """The queue is rebuilt empty on every start.

    Nothing used to look at what was already in the database, so a job that was
    pending or running when the package stopped -- an update, a reboot, a crash
    mid-scan -- stayed in that state for ever. The Jobs page showed a permanent
    "running" row, the dashboard counted it as active work, and the Stop button
    could not help: `cancel` only rewrites a PENDING row and otherwise reports
    whether the job is the one currently in flight. An orphan is neither.
    """
    await init_db()
    async with SessionFactory() as s:
        orphan_scan = Job(kind="orphan-probe-scan", state=JobState.RUNNING,
                          total=3801, processed=200)
        orphan_enrich = Job(kind="orphan-probe-enrich", state=JobState.PENDING)
        s.add_all([orphan_scan, orphan_enrich])
        await s.commit()
        ids = (orphan_scan.id, orphan_enrich.id)

    # The reconciliation directly, not through start(). The runner is a
    # singleton whose task belongs to whichever loop first started it, so
    # driving start()/stop() from a test loop fights the event loop instead of
    # exercising the behaviour -- see the note in conftest.
    await reconcile_orphaned_jobs()

    # Scoped to these two rows. Asserting over the whole table would make the
    # test depend on what every other module happened to leave queued.
    async with SessionFactory() as s:
        rows = (await s.execute(
            select(Job).where(Job.id.in_(ids)))).scalars().all()
        assert len(rows) == 2
        for row in rows:
            assert row.state == JobState.CANCELLED, \
                f"{row.kind} survived the restart still marked {row.state}"
            assert "restart" in (row.message or ""), \
                f"{row.kind} does not say why it stopped"
            assert row.finished_at is not None
