from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.quarantine import QuarantineError, QuarantineManager
from app.db.base import get_session
from app.db.models import QuarantineItem
from app.services.job_runner import runner

router = APIRouter()


class PurgeRequest(BaseModel):
    # Defaults to a dry run: the caller has to ask for the destructive form.
    dry_run: bool = True


@router.get("")
async def list_items(restored: bool = False, limit: int = 100, offset: int = 0,
                     s: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(QuarantineItem).where(QuarantineItem.restored == restored)
    total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await s.execute(
        stmt.order_by(QuarantineItem.moved_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    return {
        "total": total,
        "items": [
            {"id": r.id, "original_path": r.original_path,
             "quarantine_path": r.quarantine_path, "size_bytes": r.size_bytes,
             "reason": r.reason, "group_id": r.group_id, "moved_at": r.moved_at,
             "purge_after": r.purge_after, "restored": r.restored}
            for r in rows
        ],
    }


@router.get("/stats")
async def stats(s: AsyncSession = Depends(get_session)) -> dict:
    return await QuarantineManager(s).stats()


@router.post("/{item_id}/restore")
async def restore(item_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    try:
        dest = await QuarantineManager(s).restore(item_id)
    except QuarantineError as exc:
        raise HTTPException(400, str(exc)) from exc
    await s.commit()
    return {"id": item_id, "restored_to": str(dest)}


@router.post("/groups/{group_id}/restore")
async def restore_group(group_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    """Restore a whole group, reporting per item.

    Answers 200 with the failures listed rather than 400 on the first one. The
    manager commits each restore as it succeeds, so a partial result is a real
    state of the world, and the old behaviour -- 400, no commit, files already
    moved -- left rows that could never be restored again.
    """
    outcome = await QuarantineManager(s).restore_group(group_id)
    return {
        "group_id": group_id,
        "restored": [str(p) for p in outcome["restored"]],
        "failed": outcome["failed"],
    }


@router.post("/purge")
async def purge(body: PurgeRequest = Body(default=PurgeRequest())) -> dict:
    """Permanently delete expired items. Blocked unless hard_delete_allowed is on.

    There is no `force` option. It used to exist as a query parameter that was
    passed straight to the purge routine and skipped the `hard_delete_allowed`
    check, so a single GET-shaped request destroyed the user's files regardless
    of how the setting was configured. Enabling permanent deletion is now a
    deliberate settings change.
    """
    # Answered here as well as inside the job. The block is real either way --
    # QuarantineManager.purge refuses regardless of how it was reached, and
    # that stays the enforcement point -- but a caller who asks for a purge
    # with permanent deletion switched off used to get a job id and a 200, and
    # the refusal then appeared only in the job's result. The interface said
    # "started" and nothing happened.
    #
    # Not a dry run, which writes nothing and is how the user previews what
    # would go.
    if not body.dry_run and not get_settings().hard_delete_allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Permanent deletion is off. Quarantined files are kept until you "
            "turn on 'Allow permanent deletion' in Settings; until then they "
            "can always be restored.")

    job_id = await runner.submit("quarantine_purge", {}, dry_run=body.dry_run)
    return {"job_id": job_id, "dry_run": body.dry_run}
