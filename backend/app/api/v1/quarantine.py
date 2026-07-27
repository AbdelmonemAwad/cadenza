from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.quarantine import QuarantineError, QuarantineManager
from app.db.base import get_session
from app.db.models import QuarantineItem
from app.services.job_runner import runner

router = APIRouter()


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
    try:
        paths = await QuarantineManager(s).restore_group(group_id)
    except QuarantineError as exc:
        raise HTTPException(400, str(exc)) from exc
    await s.commit()
    return {"group_id": group_id, "restored": [str(p) for p in paths]}


@router.post("/purge")
async def purge(force: bool = False, dry_run: bool = True) -> dict:
    """Permanently delete expired items. Blocked unless hard_delete_allowed is on."""
    job_id = await runner.submit("quarantine_purge", {"force": force}, dry_run=dry_run)
    return {"job_id": job_id, "dry_run": dry_run}
