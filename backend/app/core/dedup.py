"""Advanced duplicate detection engine.

Four layers, cheapest first; every path is attributed to the strongest layer
that detects it:

  1) EXACT_FILE   -- SHA256 over the whole file (literal copies)
  2) EXACT_AUDIO  -- MD5 over the audio stream (same audio, different tags)
  3) ACOUSTIC     -- Chromaprint / AcoustID (same song, different format or bitrate)
  4) METADATA     -- high-confidence textual match (fallback when no fingerprint)

Layer 3 is the expensive one, so it applies two-dimensional blocking:
  * a duration window of +/- duration_tolerance
  * LSH bands derived from the fingerprint
which brings the cost down from O(n^2) towards O(n*k).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import fingerprint as fp
from app.core.decision import Candidate, DecisionEngine, Verdict
from app.db.models import (
    DupAction,
    DupKind,
    DuplicateGroup,
    DuplicateMember,
    Track,
    TrackStatus,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, int, int], None] | None


# --------------------------- Text normalisation ---------------------------

_PAREN_NOISE = re.compile(
    r"\((?:official|audio|video|lyrics?|hd|hq|remaster(?:ed)?[^)]*|explicit|clean|"
    r"radio edit|album version|bonus track)[^)]*\)|"
    r"\[(?:official|audio|video|lyrics?|remaster(?:ed)?[^\]]*)[^\]]*\]",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s*[\(\[]?\b(?:feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Arabic diacritics and tatweel break textual matching, and the same word is
# routinely spelled with different hamza/ya/ta-marbuta forms.
_ARABIC_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_FOLDING = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",  # alef forms
    "ى": "ي", "ئ": "ي",                                          # ya forms
    "ؤ": "و",                                                              # waw hamza
    "ة": "ه",                                                              # ta marbuta
})


def normalize_text(s: str | None) -> str:
    """Normalisation that works for both Arabic and Latin script."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = _PAREN_NOISE.sub(" ", s)
    s = _FEAT.sub("", s)
    s = _ARABIC_DIACRITICS.sub("", s)
    s = s.translate(_ARABIC_FOLDING)
    s = _NONWORD.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def metadata_key(t: Track) -> str | None:
    """Blocking key: leading title words + artist prefix + coarse duration."""
    title = normalize_text(t.title)
    artist = normalize_text(t.artist or t.albumartist)
    if not title or not t.duration:
        return None
    head = " ".join(title.split()[:3])
    bucket = int(round(t.duration / 5.0))     # 5-second window
    return f"{head}|{artist[:24]}|{bucket}"


# ----------------------------- Engine results -----------------------------

@dataclass(slots=True)
class Cluster:
    kind: DupKind
    signature: str
    confidence: float
    track_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class DedupReport:
    scanned: int = 0
    clusters: list[Cluster] = field(default_factory=list)
    comparisons: int = 0

    @property
    def duplicate_files(self) -> int:
        return sum(max(0, len(c.track_ids) - 1) for c in self.clusters)


class _UnionFind:
    """Keeps a path from appearing in more than one cluster."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for node in list(self.parent):
            out[self.find(node)].append(node)
        return {k: v for k, v in out.items() if len(v) > 1}


# --------------------------------- Engine ---------------------------------

class DeduplicationEngine:
    def __init__(self, session: AsyncSession, progress: ProgressCb = None) -> None:
        self.s = get_settings()
        self.session = session
        self.progress = progress
        self.decider = DecisionEngine()

    def _emit(self, stage: str, done: int, total: int) -> None:
        if self.progress:
            try:
                self.progress(stage, done, total)
            except Exception:      # progress reporting must never fail the job
                log.debug("progress callback failed", exc_info=True)

    async def _load_tracks(self, scope_prefix: str | None = None) -> list[Track]:
        stmt = select(Track).where(Track.status == TrackStatus.ACTIVE)
        if scope_prefix:
            stmt = stmt.where(Track.path.startswith(scope_prefix))
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    # ---------------- Layer 1: whole-file match ----------------

    @staticmethod
    def find_exact_file(tracks: Sequence[Track]) -> list[Cluster]:
        by_hash: dict[str, list[Track]] = defaultdict(list)
        for t in tracks:
            if t.sha256:
                by_hash[t.sha256].append(t)
        return [
            Cluster(DupKind.EXACT_FILE, h, 1.0, [t.id for t in group])
            for h, group in by_hash.items() if len(group) > 1
        ]

    # ---------------- Layer 2: audio-stream match ----------------

    @staticmethod
    def find_exact_audio(tracks: Sequence[Track], taken: set[int]) -> list[Cluster]:
        by_md5: dict[str, list[Track]] = defaultdict(list)
        for t in tracks:
            if t.audio_md5 and t.id not in taken:
                by_md5[t.audio_md5].append(t)
        return [
            Cluster(DupKind.EXACT_AUDIO, m, 0.99, [t.id for t in group])
            for m, group in by_md5.items() if len(group) > 1
        ]

    # ---------------- Layer 3: acoustic fingerprint ----------------

    def find_acoustic(self, tracks: Sequence[Track], taken: set[int]) -> tuple[list[Cluster], int]:
        """Group by known AcoustID first, then compare fingerprints within buckets."""
        pool = [t for t in tracks if t.id not in taken and t.fingerprint and t.duration]
        clusters: list[Cluster] = []
        uf = _UnionFind()
        comparisons = 0

        # 3a: the same AcoustID means the same recording, with high confidence.
        by_acoustid: dict[str, list[Track]] = defaultdict(list)
        for t in pool:
            if t.acoustid:
                by_acoustid[t.acoustid].append(t)
        acoustid_matched: set[int] = set()
        for aid, group in by_acoustid.items():
            if len(group) > 1:
                clusters.append(Cluster(DupKind.ACOUSTIC, f"acoustid:{aid}", 0.97,
                                        [t.id for t in group]))
                acoustid_matched.update(t.id for t in group)

        # 3b: pairwise fingerprint comparison for tracks without an AcoustID.
        remaining = [t for t in pool if t.id not in acoustid_matched]
        blocks: dict[tuple[int, str], list[Track]] = defaultdict(list)
        tol = max(1.0, self.s.duration_tolerance_s)
        for t in remaining:
            center = int(t.duration // tol)
            # Index under every LSH band: one flipped bit must not hide a pair.
            # Also index in the adjacent duration slot so pairs straddling a
            # slot boundary still meet.
            for bucket in fp.bucket_keys(t.fingerprint or ""):
                blocks[(center, bucket)].append(t)
                blocks[(center + 1, bucket)].append(t)

        threshold = self.s.acoustic_match_threshold
        total_blocks = len(blocks)
        for idx, members in enumerate(blocks.values()):
            if idx % 200 == 0:
                self._emit("acoustic", idx, total_blocks)
            if len(members) < 2 or len(members) > 400:
                # An oversized bucket means a badly distributed band; skipping
                # it is cheaper than an O(n^2) sweep, and other bands still
                # give these tracks a chance to meet.
                continue
            seen_pairs: set[tuple[int, int]] = set()
            for i in range(len(members)):
                a = members[i]
                for j in range(i + 1, len(members)):
                    b = members[j]
                    pair = (min(a.id, b.id), max(a.id, b.id))
                    if pair in seen_pairs or uf.find(a.id) == uf.find(b.id):
                        continue
                    seen_pairs.add(pair)
                    if abs((a.duration or 0) - (b.duration or 0)) > self.s.duration_tolerance_s:
                        continue
                    comparisons += 1
                    if fp.similarity(a.fingerprint or "", b.fingerprint or "") >= threshold:
                        uf.union(a.id, b.id)

        for root, ids in uf.groups().items():
            clusters.append(Cluster(DupKind.ACOUSTIC, f"fpgroup:{root}", 0.92, sorted(ids)))
        self._emit("acoustic", total_blocks, total_blocks)
        return clusters, comparisons

    # ---------------- Layer 4: textual match ----------------

    def find_metadata(self, tracks: Sequence[Track], taken: set[int]) -> list[Cluster]:
        """Safety net for files without a fingerprint (partly damaged, or no fpcalc)."""
        pool = [t for t in tracks if t.id not in taken]
        blocks: dict[str, list[Track]] = defaultdict(list)
        for t in pool:
            key = metadata_key(t)
            if key:
                blocks[key].append(t)

        clusters: list[Cluster] = []
        threshold = self.s.title_fuzzy_threshold
        for key, members in blocks.items():
            if len(members) < 2 or len(members) > 60:
                continue
            uf = _UnionFind()
            for i in range(len(members)):
                a = members[i]
                na_title = normalize_text(a.title)
                na_artist = normalize_text(a.artist or a.albumartist)
                for j in range(i + 1, len(members)):
                    b = members[j]
                    if abs((a.duration or 0) - (b.duration or 0)) > self.s.duration_tolerance_s:
                        continue
                    if fuzz.token_sort_ratio(na_title, normalize_text(b.title)) < threshold:
                        continue
                    artist_sim = fuzz.token_set_ratio(
                        na_artist, normalize_text(b.artist or b.albumartist))
                    if artist_sim < threshold - 8:
                        continue
                    uf.union(a.id, b.id)
            for root, ids in uf.groups().items():
                clusters.append(Cluster(DupKind.METADATA, f"meta:{key}:{root}", 0.80, sorted(ids)))
        return clusters

    # ---------------- Orchestration ----------------

    async def analyze(self, scope_prefix: str | None = None,
                      use_acoustic: bool | None = None) -> DedupReport:
        tracks = await self._load_tracks(scope_prefix)
        by_id = {t.id: t for t in tracks}
        report = DedupReport(scanned=len(tracks))
        taken: set[int] = set()

        self._emit("exact_file", 0, 4)
        for c in self.find_exact_file(tracks):
            report.clusters.append(c)
            taken.update(c.track_ids)

        self._emit("exact_audio", 1, 4)
        for c in self.find_exact_audio(tracks, taken):
            report.clusters.append(c)
            taken.update(c.track_ids)

        if use_acoustic if use_acoustic is not None else self.s.acoustic_enabled:
            self._emit("acoustic", 2, 4)
            acoustic, comparisons = await asyncio.to_thread(self.find_acoustic, tracks, taken)
            report.comparisons += comparisons
            for c in acoustic:
                report.clusters.append(c)
                taken.update(c.track_ids)

        self._emit("metadata", 3, 4)
        for c in await asyncio.to_thread(self.find_metadata, tracks, taken):
            report.clusters.append(c)
            taken.update(c.track_ids)

        self._emit("persist", 4, 4)
        await self._persist(report, by_id)
        return report

    async def _persist(self, report: DedupReport, by_id: dict[int, Track]) -> None:
        """Store clusters and decision-engine verdicts. Files are never touched."""
        existing = await self.session.execute(
            select(DuplicateGroup).where(DuplicateGroup.resolved == False))  # noqa: E712
        for g in existing.scalars().all():
            await self.session.delete(g)
        await self.session.flush()

        for cluster in report.clusters:
            members = [by_id[i] for i in cluster.track_ids if i in by_id]
            if len(members) < 2:
                continue
            candidates = [Candidate.from_track(t) for t in members]
            verdict: Verdict | None = self.decider.decide(candidates)
            if verdict is None:
                continue

            group = DuplicateGroup(
                kind=cluster.kind, signature=cluster.signature,
                confidence=cluster.confidence, member_count=len(members),
                reclaimable_bytes=sum(c.size_bytes for c in verdict.losers),
                resolved=False,
            )
            self.session.add(group)
            await self.session.flush()

            # An ambiguous or low-confidence cluster is never proposed for
            # automatic removal -- it waits for a human decision.
            auto = (not verdict.ambiguous) and cluster.confidence >= 0.90

            for cand in candidates:
                is_keeper = cand.track_id == verdict.keep.track_id
                action = (
                    DupAction.KEEP if is_keeper
                    else (DupAction.QUARANTINE if auto else DupAction.SKIP)
                )
                self.session.add(DuplicateMember(
                    group_id=group.id, track_id=cand.track_id,
                    score=verdict.scores[cand.track_id],
                    score_breakdown=verdict.breakdowns[cand.track_id],
                    proposed_action=action,
                    reason=verdict.reasons.get(cand.track_id),
                ))
        await self.session.commit()


# --------------------------- Dry-run reporting ---------------------------

async def build_dry_run_report(session: AsyncSession, limit: int | None = None) -> dict:
    """Report ready to display or export before any deletion is executed."""
    stmt = select(DuplicateGroup).where(DuplicateGroup.resolved == False)  # noqa: E712
    stmt = stmt.order_by(DuplicateGroup.reclaimable_bytes.desc())
    if limit:
        stmt = stmt.limit(limit)
    groups = list((await session.execute(stmt)).scalars().all())

    total_reclaim = sum(g.reclaimable_bytes for g in groups)
    by_kind: dict[str, int] = defaultdict(int)
    payload = []
    for g in groups:
        by_kind[g.kind if isinstance(g.kind, str) else g.kind.value] += 1
        payload.append({
            "group_id": g.id,
            "kind": g.kind if isinstance(g.kind, str) else g.kind.value,
            "confidence": g.confidence,
            "reclaimable_bytes": g.reclaimable_bytes,
            "members": [
                {
                    "track_id": m.track_id,
                    "path": m.track.path,
                    "codec": m.track.codec,
                    "bitrate": m.track.bitrate,
                    "duration": m.track.duration,
                    "size_bytes": m.track.size_bytes,
                    "score": round(m.score, 4),
                    "breakdown": m.score_breakdown,
                    "action": m.proposed_action if isinstance(m.proposed_action, str)
                    else m.proposed_action.value,
                    "reason": m.reason,
                }
                for m in sorted(g.members, key=lambda x: x.score, reverse=True)
            ],
        })

    return {
        "dry_run": True,
        "groups": len(groups),
        "duplicate_files": sum(max(0, g.member_count - 1) for g in groups),
        "reclaimable_bytes": total_reclaim,
        "reclaimable_human": human_bytes(total_reclaim),
        "by_kind": dict(by_kind),
        "details": payload,
    }


def human_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.2f} {unit}"
        v /= 1024
    return f"{v:.2f} TB"
