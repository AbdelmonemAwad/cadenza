from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dedup import build_dry_run_report
from app.db.base import get_session
from app.db.models import DupAction, DuplicateGroup
from app.services.job_runner import runner

router = APIRouter()


class AnalyzeRequest(BaseModel):
    path: str | None = None
    acoustic: bool | None = None
    limit: int = 500


class ApplyRequest(BaseModel):
    group_ids: list[int] | None = None
    dry_run: bool = True


class MemberOverride(BaseModel):
    track_id: int
    action: str = Field(pattern="^(keep|quarantine|skip)$")


@router.post("/analyze")
async def analyze(req: AnalyzeRequest = Body(default=AnalyzeRequest())) -> dict:
    """Run duplicate analysis as a background job. Nothing is deleted."""
    job_id = await runner.submit("dedup_analyze", req.model_dump(), dry_run=True)
    return {"job_id": job_id}


@router.get("/report")
async def report(limit: int = Query(200, le=2000),
                 s: AsyncSession = Depends(get_session)) -> dict:
    """Full preview report to review before anything is executed."""
    return await build_dry_run_report(s, limit=limit)


@router.get("/groups")
async def groups(kind: str | None = None, resolved: bool = False,
                 min_bytes: int = 0, limit: int = 100, offset: int = 0,
                 s: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(DuplicateGroup).where(DuplicateGroup.resolved == resolved)
    if kind:
        stmt = stmt.where(DuplicateGroup.kind == kind)
    if min_bytes:
        stmt = stmt.where(DuplicateGroup.reclaimable_bytes >= min_bytes)
    total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await s.execute(
        stmt.order_by(DuplicateGroup.reclaimable_bytes.desc())
        .offset(offset).limit(limit))).scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": g.id,
                "kind": g.kind if isinstance(g.kind, str) else g.kind.value,
                "confidence": g.confidence,
                "member_count": g.member_count,
                "reclaimable_bytes": g.reclaimable_bytes,
                "members": [
                    {"track_id": m.track_id, "path": m.track.path,
                     "codec": m.track.codec, "bitrate": m.track.bitrate,
                     "lossless": m.track.lossless, "duration": m.track.duration,
                     "size_bytes": m.track.size_bytes, "score": round(m.score, 4),
                     "breakdown": m.score_breakdown, "reason": m.reason,
                     "action": m.proposed_action if isinstance(m.proposed_action, str)
                     else m.proposed_action.value,
                     "applied": m.applied}
                    for m in sorted(g.members, key=lambda x: x.score, reverse=True)
                ],
            }
            for g in rows
        ],
    }


@router.patch("/groups/{group_id}/members")
async def override_actions(group_id: int, overrides: list[MemberOverride],
                           s: AsyncSession = Depends(get_session)) -> dict:
    """Override the engine's verdict before execution."""
    group = await s.get(DuplicateGroup, group_id)
    if group is None:
        raise HTTPException(404, "duplicate group not found")

    by_track = {m.track_id: m for m in group.members}
    changed = 0
    for o in overrides:
        member = by_track.get(o.track_id)
        if member is None:
            continue
        member.proposed_action = DupAction(o.action)
        member.reason = f"{member.reason or ''} (manually overridden)".strip()
        changed += 1

    keepers = sum(1 for m in group.members if _action(m) == DupAction.KEEP.value)
    if keepers == 0:
        # Guard rail: a cluster where every copy is quarantined loses the song.
        raise HTTPException(400, "at least one copy must be kept")

    await s.commit()
    return {"group_id": group_id, "updated": changed, "keepers": keepers}


@router.post("/apply")
async def apply(req: ApplyRequest = Body(default=ApplyRequest())) -> dict:
    """Move flagged duplicates into quarantine (or preview when dry_run=true)."""
    job_id = await runner.submit(
        "dedup_apply", {"group_ids": req.group_ids}, dry_run=req.dry_run)
    return {"job_id": job_id, "dry_run": req.dry_run}


@router.post("/groups/{group_id}/ignore")
async def ignore_group(group_id: int, s: AsyncSession = Depends(get_session)) -> dict:
    group = await s.get(DuplicateGroup, group_id)
    if group is None:
        raise HTTPException(404, "duplicate group not found")
    group.resolved = True
    for m in group.members:
        m.proposed_action = DupAction.SKIP
    await s.commit()
    return {"group_id": group_id, "ignored": True}


def _action(member) -> str:
    a = member.proposed_action
    return a if isinstance(a, str) else a.value
