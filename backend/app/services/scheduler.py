"""Recurring task scheduling (APScheduler + cron), persisted in the database."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import ScheduledTask
from app.services.job_runner import runner

log = logging.getLogger(__name__)

DEFAULT_TASKS = [
    {"name": "Nightly scan", "job_kind": "scan", "cron": "0 3 * * *",
     "params": {"full": False}, "enabled": True},
    {"name": "Weekly duplicate analysis", "job_kind": "dedup_analyze", "cron": "30 3 * * 0",
     "params": {}, "enabled": True},
    {"name": "Enrich incomplete tags", "job_kind": "enrich", "cron": "0 4 * * *",
     "params": {"only_incomplete": True, "limit": 300}, "enabled": False},
    {"name": "Purge expired quarantine", "job_kind": "quarantine_purge", "cron": "0 5 * * 1",
     "params": {}, "enabled": False},
]


class TaskScheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    async def start(self) -> None:
        await self._seed_defaults()
        self.scheduler.start()
        await self.reload()
        log.info("scheduler started with %d job(s)", len(self.scheduler.get_jobs()))

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _seed_defaults(self) -> None:
        async with session_scope() as s:
            existing = {
                r.name for r in (await s.execute(select(ScheduledTask))).scalars().all()
            }
            for spec in DEFAULT_TASKS:
                if spec["name"] not in existing:
                    s.add(ScheduledTask(**spec))

    async def reload(self) -> None:
        """Rebuild the schedule from the database after any UI change."""
        self.scheduler.remove_all_jobs()
        async with session_scope() as s:
            tasks = list((await s.execute(
                select(ScheduledTask).where(ScheduledTask.enabled == True)  # noqa: E712
            )).scalars().all())

        for t in tasks:
            try:
                trigger = CronTrigger.from_crontab(t.cron, timezone="UTC")
            except ValueError as exc:
                log.warning("invalid cron for task %s (%s): %s", t.name, t.cron, exc)
                continue
            self.scheduler.add_job(
                self._fire, trigger, id=f"task-{t.id}", name=t.name,
                args=[t.id, t.job_kind, dict(t.params or {})],
                replace_existing=True, misfire_grace_time=3600, coalesce=True,
                max_instances=1,
            )
        await self._persist_next_runs()

    async def _fire(self, task_id: int, kind: str, params: dict) -> None:
        log.info("scheduled task %s -> job %s", task_id, kind)
        try:
            await runner.submit(kind, params)
        finally:
            async with session_scope() as s:
                task = await s.get(ScheduledTask, task_id)
                if task:
                    task.last_run = datetime.now(UTC).replace(tzinfo=None)
            await self._persist_next_runs()

    async def _persist_next_runs(self) -> None:
        async with session_scope() as s:
            for job in self.scheduler.get_jobs():
                if not job.id.startswith("task-"):
                    continue
                task = await s.get(ScheduledTask, int(job.id.removeprefix("task-")))
                if task and job.next_run_time:
                    task.next_run = job.next_run_time.replace(tzinfo=None)


scheduler = TaskScheduler()
