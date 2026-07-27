"""Background job execution and live progress broadcasting."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.dedup import DeduplicationEngine, build_dry_run_report
from app.core.organizer import Organizer
from app.core.quarantine import QuarantineManager
from app.core.scanner import LibraryScanner
from app.core.transcode import LEGACY_CODECS, PRESETS, Transcoder
from app.db.base import session_scope
from app.db.models import (
    AuditLog,
    DupAction,
    DuplicateGroup,
    Job,
    JobState,
    Track,
    TrackStatus,
)
from app.services.enrichment import EnrichmentService

log = logging.getLogger(__name__)

JobHandler = Callable[[int, dict, bool, "JobRunner"], Awaitable[dict]]


class JobRunner:
    """Single-concurrency queue: prevents two jobs from racing on the same files."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._current: int | None = None
        self._cancel_flags: set[int] = set()
        self._subscribers: set[asyncio.Queue] = set()
        self.handlers: dict[str, JobHandler] = {
            "scan": handle_scan,
            "dedup_analyze": handle_dedup_analyze,
            "dedup_apply": handle_dedup_apply,
            "enrich": handle_enrich,
            "organize": handle_organize,
            "convert": handle_convert,
            "quarantine_purge": handle_purge,
        }

    # ---- Lifecycle ----

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="cadenza-job-runner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            # Awaiting the cancelled task is what lets its `finally` blocks run.
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def submit(self, kind: str, params: dict | None = None,
                     dry_run: bool | None = None) -> int:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind: {kind}")
        dry = self.settings.dry_run_default if dry_run is None else dry_run
        async with session_scope() as s:
            job = Job(kind=kind, params=params or {}, dry_run=dry, state=JobState.PENDING)
            s.add(job)
            await s.flush()
            job_id = job.id
        await self._queue.put(job_id)
        await self.broadcast({"type": "job.queued", "job_id": job_id, "kind": kind})
        return job_id

    async def cancel(self, job_id: int) -> bool:
        self._cancel_flags.add(job_id)
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job and job.state == JobState.PENDING:
                job.state = JobState.CANCELLED
                return True
        return job_id == self._current

    def is_cancelled(self, job_id: int) -> bool:
        return job_id in self._cancel_flags

    # ---- Main loop ----

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            self._current = job_id
            try:
                await self._run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed", job_id)
            finally:
                self._current = None
                self._cancel_flags.discard(job_id)
                self._queue.task_done()

    async def _run(self, job_id: int) -> None:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job is None or job.state == JobState.CANCELLED:
                return
            job.state = JobState.RUNNING
            job.started_at = datetime.now(UTC).replace(tzinfo=None)
            kind, params, dry_run = job.kind, dict(job.params or {}), job.dry_run

        await self.broadcast({"type": "job.started", "job_id": job_id, "kind": kind})
        try:
            result = await self.handlers[kind](job_id, params, dry_run, self)
            state, message = JobState.DONE, None
        except Exception as exc:
            log.exception("job %s (%s) failed", job_id, kind)
            result, state, message = {}, JobState.FAILED, str(exc)[:1000]

        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job:
                job.state = state
                job.result = result
                job.message = message
                job.finished_at = datetime.now(UTC).replace(tzinfo=None)
            s.add(AuditLog(action=f"job.{kind}", job_id=job_id,
                           level="error" if state == JobState.FAILED else "info",
                           detail={"state": state.value, "result_keys": list(result)}))
        await self.broadcast({"type": "job.finished", "job_id": job_id,
                              "state": state.value, "message": message,
                              "result": _trim(result)})

    # ---- Progress and broadcasting ----

    async def progress(self, job_id: int, processed: int, total: int,
                       message: str | None = None) -> None:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job:
                job.processed, job.total = processed, total
                if message:
                    job.message = message[:500]
        await self.broadcast({"type": "job.progress", "job_id": job_id,
                              "processed": processed, "total": total,
                              "message": message})

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def broadcast(self, payload: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop a stalled subscriber rather than block the running job.
                self._subscribers.discard(q)


def _trim(d: dict, limit: int = 4000) -> dict:
    """Keep the WebSocket payload small; the full result stays in the database."""
    try:
        if len(json.dumps(d, default=str)) <= limit:
            return d
    except (TypeError, ValueError):
        pass
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else "...")
            for k, v in d.items()}


# ------------------------------- Handlers -------------------------------

async def handle_scan(job_id: int, params: dict, dry_run: bool, runner: JobRunner) -> dict:
    root = Path(params.get("path") or runner.settings.music_root)

    async def progress(done: int, total: int, current: str) -> None:
        await runner.progress(job_id, done, total, current)

    async with session_scope() as s:
        stats = await LibraryScanner(s, progress).scan(
            root, full=bool(params.get("full")),
            compute_fingerprints=params.get("fingerprints", True),
            compute_audio_md5=params.get("audio_md5", True),
        )
    return stats.as_dict()


async def handle_dedup_analyze(job_id: int, params: dict, dry_run: bool,
                               runner: JobRunner) -> dict:
    loop = asyncio.get_running_loop()

    def progress(stage: str, done: int, total: int) -> None:
        # The engine's hot loop runs in a worker thread, so hop back to the loop.
        asyncio.run_coroutine_threadsafe(runner.progress(job_id, done, total, stage), loop)

    async with session_scope() as s:
        report = await DeduplicationEngine(s, progress).analyze(
            scope_prefix=params.get("path"),
            use_acoustic=params.get("acoustic"),
        )
        summary = await build_dry_run_report(s, limit=params.get("limit", 500))

    summary["engine"] = {
        "scanned": report.scanned,
        "clusters": len(report.clusters),
        "duplicate_files": report.duplicate_files,
        "fingerprint_comparisons": report.comparisons,
    }
    return summary


async def handle_dedup_apply(job_id: int, params: dict, dry_run: bool,
                             runner: JobRunner) -> dict:
    group_ids: list[int] | None = params.get("group_ids")
    moved = failed = freed = 0
    errors: list[str] = []

    async with session_scope() as s:
        stmt = select(DuplicateGroup).where(DuplicateGroup.resolved == False)  # noqa: E712
        if group_ids:
            stmt = stmt.where(DuplicateGroup.id.in_(group_ids))
        groups = list((await s.execute(stmt)).scalars().all())
        manager = QuarantineManager(s)
        total = sum(len(g.members) for g in groups)
        done = 0

        for g in groups:
            if runner.is_cancelled(job_id):
                break
            for m in g.members:
                done += 1
                if done % 20 == 0:
                    await runner.progress(job_id, done, total, f"group {g.id}")
                action = m.proposed_action
                action = action if isinstance(action, str) else action.value
                if action != DupAction.QUARANTINE.value:
                    continue
                if dry_run:
                    moved += 1
                    freed += m.track.size_bytes or 0
                    continue
                try:
                    await manager.quarantine(m.track, reason=f"duplicate:{g.kind}",
                                             group_id=g.id, job_id=job_id)
                    m.applied = True
                    moved += 1
                    freed += m.track.size_bytes or 0
                except Exception as exc:
                    failed += 1
                    errors.append(f"{m.track.path}: {exc}")
            if not dry_run:
                g.resolved = True

    return {"dry_run": dry_run, "quarantined": moved, "failed": failed,
            "freed_bytes": freed, "errors": errors[:50]}


async def handle_enrich(job_id: int, params: dict, dry_run: bool,
                        runner: JobRunner) -> dict:
    track_ids: list[int] | None = params.get("track_ids")
    limit = int(params.get("limit", 500))
    results: list[dict] = []
    applied = skipped = failed = 0

    async with session_scope() as s:
        stmt = select(Track).where(Track.status == TrackStatus.ACTIVE)
        if track_ids:
            stmt = stmt.where(Track.id.in_(track_ids))
        elif params.get("only_incomplete", True):
            stmt = stmt.where(Track.tag_completeness < 0.85)
        stmt = stmt.order_by(Track.tag_completeness.asc()).limit(limit)
        tracks = list((await s.execute(stmt)).scalars().all())

        service = EnrichmentService(s)
        try:
            total = len(tracks)
            for i, t in enumerate(tracks, 1):
                if runner.is_cancelled(job_id):
                    break
                r = await service.enrich(
                    t, dry_run=dry_run,
                    overwrite=bool(params.get("overwrite", False)),
                    min_confidence=float(params.get("min_confidence", 0.55)),
                    want_artwork=params.get("artwork", True),
                    want_lyrics=params.get("lyrics", True),
                    job_id=job_id,
                )
                if r.error:
                    failed += 1
                elif r.applied or (dry_run and r.changed_fields):
                    applied += 1
                else:
                    skipped += 1
                results.append({
                    "track_id": r.track_id, "path": r.path,
                    "confidence": r.confidence, "applied": r.applied,
                    "changed": {k: list(v) for k, v in r.changed_fields.items()},
                    "sources": r.field_sources, "conflicts": r.conflicts,
                    "artwork": r.artwork, "lyrics": r.lyrics, "error": r.error,
                })
                if i % 5 == 0:
                    await runner.progress(job_id, i, total, t.filename)
        finally:
            await service.aclose()

    return {"dry_run": dry_run, "processed": len(results), "applied": applied,
            "skipped": skipped, "failed": failed, "items": results[:300]}


async def handle_organize(job_id: int, params: dict, dry_run: bool,
                          runner: JobRunner) -> dict:
    async with session_scope() as s:
        stmt = select(Track).where(Track.status == TrackStatus.ACTIVE)
        if params.get("track_ids"):
            stmt = stmt.where(Track.id.in_(params["track_ids"]))
        if params.get("path"):
            stmt = stmt.where(Track.path.startswith(params["path"]))
        tracks = list((await s.execute(stmt)).scalars().all())

        organizer = Organizer(s)
        plans = organizer.plan(tracks, params.get("template"))
        await runner.progress(job_id, 0, len(plans), "planning")
        out = await organizer.apply(plans, dry_run=dry_run, job_id=job_id,
                                    tracks_by_id={t.id: t for t in tracks})
    out["preview"] = [
        {"track_id": p.track_id, "from": p.src, "to": p.dst, "reason": p.reason}
        for p in plans if p.changed
    ][:300]
    return out


async def handle_convert(job_id: int, params: dict, dry_run: bool,
                         runner: JobRunner) -> dict:
    """Audio format conversion. Selection is either explicit, by source codec,
    or the engine's own suggestions for legacy and oversized files."""
    preset = params.get("preset", "flac")
    if preset not in PRESETS:
        raise ValueError(f"unknown preset '{preset}'; available: {sorted(PRESETS)}")

    keep_original = bool(params.get("keep_original", True))
    dest_dir = Path(params["dest_dir"]) if params.get("dest_dir") else None
    transcoder = Transcoder()

    async with session_scope() as s:
        stmt = select(Track).where(Track.status == TrackStatus.ACTIVE)
        if params.get("track_ids"):
            stmt = stmt.where(Track.id.in_(params["track_ids"]))
        elif params.get("source_codecs"):
            stmt = stmt.where(Track.codec.in_(tuple(params["source_codecs"])))
        elif params.get("legacy_only", True):
            stmt = stmt.where(Track.codec.in_(tuple(LEGACY_CODECS)))
        stmt = stmt.limit(int(params.get("limit", 200)))
        tracks = list((await s.execute(stmt)).scalars().all())

    items = [(Path(t.path), preset) for t in tracks if Path(t.path).is_file()]
    await runner.progress(job_id, 0, len(items), f"preset={preset}")
    results = await transcoder.batch(items, dry_run=dry_run,
                                     keep_original=keep_original, dest_dir=dest_dir)

    converted = sum(1 for r in results if r.ok)
    return {
        "dry_run": dry_run, "preset": preset, "total": len(results),
        "converted": converted, "failed": len(results) - converted,
        "saved_bytes": sum(r.saved_bytes for r in results),
        "items": [{"src": r.src, "dst": r.dst, "ok": r.ok, "error": r.error,
                   "src_bytes": r.src_bytes, "dst_bytes": r.dst_bytes}
                  for r in results[:200]],
    }


async def handle_purge(job_id: int, params: dict, dry_run: bool,
                       runner: JobRunner) -> dict:
    async with session_scope() as s:
        manager = QuarantineManager(s)
        if dry_run:
            return {"dry_run": True, **(await manager.stats())}
        return await manager.purge_expired(force=bool(params.get("force")))


runner = JobRunner()
