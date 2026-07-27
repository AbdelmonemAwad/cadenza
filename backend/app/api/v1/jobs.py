from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Job, ScheduledTask
from app.services.job_runner import runner
from app.services.scheduler import scheduler

router = APIRouter()


class SubmitRequest(BaseModel):
    kind: str
    params: dict = {}
    dry_run: bool | None = None


class TaskUpsert(BaseModel):
    name: str
    job_kind: str
    cron: str
    params: dict = {}
    enabled: bool = True


@router.get("/kinds")
async def kinds() -> list[str]:
    return sorted(runner.handlers)


@router.post("")
async def submit(req: SubmitRequest) -> dict:
    try:
        job_id = await runner.submit(req.kind, req.params, req.dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"job_id": job_id}


@router.get("")
async def list_jobs(state: str | None = None, kind: str | None = None,
                    limit: int = 50, offset: int = 0,
                    s: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(Job)
    if state:
        stmt = stmt.where(Job.state == state)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await s.execute(
        stmt.order_by(Job.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return {"total": total, "items": [_job(j) for j in rows]}


@router.get("/{job_id}")
async def get_job(job_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    job = await s.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {**_job(job), "result": job.result, "params": job.params}


@router.post("/{job_id}/cancel")
async def cancel(job_id: int) -> dict:
    return {"job_id": job_id, "cancelled": await runner.cancel(job_id)}


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Live job progress feed."""
    await ws.accept()
    queue = runner.subscribe()
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                await ws.send_text(json.dumps(payload, default=str))
            except TimeoutError:
                # Keeps the connection alive through nginx's read timeout.
                await ws.send_text('{"type":"ping"}')
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        runner.unsubscribe(queue)


# ---------------------------- Scheduling ----------------------------

@router.get("/schedule/tasks")
async def list_tasks(s: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await s.execute(select(ScheduledTask).order_by(ScheduledTask.id))).scalars().all()
    return [
        {"id": t.id, "name": t.name, "job_kind": t.job_kind, "cron": t.cron,
         "params": t.params, "enabled": t.enabled,
         "last_run": t.last_run, "next_run": t.next_run}
        for t in rows
    ]


@router.post("/schedule/tasks")
async def upsert_task(body: TaskUpsert, s: AsyncSession = Depends(get_session)) -> dict:
    if body.job_kind not in runner.handlers:
        raise HTTPException(400, f"unknown job kind: {body.job_kind}")
    existing = (await s.execute(
        select(ScheduledTask).where(ScheduledTask.name == body.name))).scalar_one_or_none()
    if existing:
        existing.job_kind, existing.cron = body.job_kind, body.cron
        existing.params, existing.enabled = body.params, body.enabled
        task_id = existing.id
    else:
        task = ScheduledTask(**body.model_dump())
        s.add(task)
        await s.flush()
        task_id = task.id
    await s.commit()
    await scheduler.reload()
    return {"id": task_id, "name": body.name}


@router.delete("/schedule/tasks/{task_id}")
async def delete_task(task_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    task = await s.get(ScheduledTask, task_id)
    if task is None:
        raise HTTPException(404, "scheduled task not found")
    await s.delete(task)
    await s.commit()
    await scheduler.reload()
    return {"deleted": task_id}


@router.post("/schedule/tasks/{task_id}/run")
async def run_now(task_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    task = await s.get(ScheduledTask, task_id)
    if task is None:
        raise HTTPException(404, "scheduled task not found")
    job_id = await runner.submit(task.job_kind, dict(task.params or {}))
    return {"job_id": job_id}


def _job(j: Job) -> dict:
    return {
        "id": j.id, "kind": j.kind,
        "state": j.state if isinstance(j.state, str) else j.state.value,
        "dry_run": j.dry_run, "processed": j.processed, "total": j.total,
        "succeeded": j.succeeded, "failed": j.failed, "message": j.message,
        "created_at": j.created_at, "started_at": j.started_at,
        "finished_at": j.finished_at,
    }
