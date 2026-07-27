"""Smart decision engine: which copy do we keep?

Produces a per-criterion score breakdown for every file in a duplicate cluster
so the verdict stays explainable and reviewable in the UI before anything is
executed. This module never touches the filesystem.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

# Format preference (0..1): lossless first, then the more efficient lossy codecs.
FORMAT_RANK: dict[str, float] = {
    "flac": 1.00, "alac": 0.97, "wavpack": 0.94, "ape": 0.92,
    "pcm_s24le": 0.90, "pcm_s16le": 0.86, "wav": 0.86, "aiff": 0.84, "dsd": 0.95,
    "opus": 0.62, "aac": 0.58, "mp3": 0.52, "vorbis": 0.50, "wmav2": 0.30, "wmapro": 0.34,
}

# Criterion weights -- overridable from settings.
DEFAULT_WEIGHTS: dict[str, float] = {
    "format": 0.30,        # lossless vs lossy
    "bitrate": 0.22,       # encoding quality
    "resolution": 0.12,    # sample rate + bit depth
    "tags": 0.16,          # metadata completeness
    "artwork": 0.08,       # presence and resolution of cover art
    "lyrics": 0.04,
    "duration": 0.05,      # longer means not truncated
    "path": 0.03,          # how sane the file's location is
}

# Path signals that suggest a secondary or throwaway copy.
_BAD_PATH_TOKENS = (
    "new folder", "copy", "duplicate", "dup", "temp", "tmp",
    "downloads", "download", "recycle", "@eadir", "unsorted",
    "incoming", "torrent",
)
_BAD_NAME_PATTERNS = ("(1)", "(2)", "(3)", " - copy", "_copy", "copy of", "duplicate")


@dataclass(slots=True)
class Candidate:
    """Flat view of a Track row -- keeps the engine testable without a database."""
    track_id: int
    path: str
    size_bytes: int = 0
    codec: str | None = None
    lossless: bool = False
    bitrate: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    duration: float | None = None
    tag_completeness: float = 0.0
    has_artwork: bool = False
    artwork_px: int | None = None
    has_lyrics: bool = False
    has_synced_lyrics: bool = False
    mb_recording_id: str | None = None
    first_seen_ts: float = 0.0

    @classmethod
    def from_track(cls, t) -> Candidate:
        return cls(
            track_id=t.id, path=t.path, size_bytes=t.size_bytes or 0,
            codec=t.codec, lossless=bool(t.lossless), bitrate=t.bitrate,
            sample_rate=t.sample_rate, bit_depth=t.bit_depth, duration=t.duration,
            tag_completeness=t.tag_completeness or 0.0,
            has_artwork=bool(t.has_artwork), artwork_px=t.artwork_px,
            has_lyrics=bool(t.has_lyrics), has_synced_lyrics=bool(t.has_synced_lyrics),
            mb_recording_id=t.mb_recording_id,
            first_seen_ts=t.first_seen.timestamp() if t.first_seen else 0.0,
        )


@dataclass(slots=True)
class Verdict:
    keep: Candidate
    losers: list[Candidate] = field(default_factory=list)
    scores: dict[int, float] = field(default_factory=dict)
    breakdowns: dict[int, dict[str, float]] = field(default_factory=dict)
    reasons: dict[int, str] = field(default_factory=dict)
    ambiguous: bool = False          # margin too small: surface it, never auto-apply


class DecisionEngine:
    """Scores each file in 0..1 and picks the highest."""

    AMBIGUITY_MARGIN = 0.03          # below this the verdict is not decisive

    def __init__(self, weights: dict[str, float] | None = None,
                 prefer_lossless: bool = True) -> None:
        self.settings = get_settings()
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.prefer_lossless = prefer_lossless

    # ----------------- Individual criteria -----------------

    def _score_format(self, c: Candidate) -> float:
        base = FORMAT_RANK.get((c.codec or "").lower(), 0.40)
        if not self.prefer_lossless and c.lossless:
            base = min(base, 0.70)
        return base

    def _score_bitrate(self, c: Candidate, peers: Sequence[Candidate]) -> float:
        """Lossless scores full marks; lossy is judged against the best lossy peer."""
        if c.lossless:
            return 1.0
        br = c.bitrate or 0
        if br <= 0:
            return 0.0
        # 320 kbps -> 1.0, 128 kbps -> ~0.4, clipped linear ramp
        norm = min(br / 320_000.0, 1.0)
        lossy_peers = [p.bitrate or 0 for p in peers if not p.lossless]
        best = max(lossy_peers) if lossy_peers else br
        relative = br / best if best else 1.0
        return round(0.6 * norm + 0.4 * relative, 4)

    def _score_resolution(self, c: Candidate) -> float:
        sr = c.sample_rate or 0
        depth = c.bit_depth or 16
        sr_score = 0.0
        if sr >= 176_400:
            sr_score = 1.0
        elif sr >= 88_200:
            sr_score = 0.9
        elif sr >= 44_100:
            sr_score = 0.75
        elif sr >= 32_000:
            sr_score = 0.45
        elif sr > 0:
            sr_score = 0.2
        depth_score = {8: 0.2, 16: 0.7, 24: 1.0, 32: 1.0}.get(depth, 0.7)
        return round(0.6 * sr_score + 0.4 * depth_score, 4)

    def _score_tags(self, c: Candidate) -> float:
        s = c.tag_completeness
        if c.mb_recording_id:
            s = min(1.0, s + 0.08)      # linked to MusicBrainz implies vetted data
        return round(s, 4)

    def _score_artwork(self, c: Candidate) -> float:
        if not c.has_artwork:
            return 0.0
        px = c.artwork_px or 0
        if px >= 1400:
            return 1.0
        if px >= 1000:
            return 0.9
        if px >= 600:
            return 0.75
        if px >= 300:
            return 0.5
        return 0.35

    def _score_lyrics(self, c: Candidate) -> float:
        if c.has_synced_lyrics:
            return 1.0
        return 0.6 if c.has_lyrics else 0.0

    def _score_duration(self, c: Candidate, peers: Sequence[Candidate]) -> float:
        """Penalises truncated copies: noticeably shorter than the longest peer."""
        durations = [p.duration for p in peers if p.duration]
        if not durations or not c.duration:
            return 0.5
        longest = max(durations)
        if longest <= 0:
            return 0.5
        ratio = c.duration / longest
        if ratio >= 0.98:
            return 1.0
        if ratio >= 0.95:
            return 0.8
        return round(max(0.0, ratio * 0.7), 4)

    def _score_path(self, c: Candidate) -> float:
        p = c.path.lower()
        name = Path(c.path).name.lower()
        score = 1.0
        for token in _BAD_PATH_TOKENS:
            if token in p:
                score -= 0.25
        for pattern in _BAD_NAME_PATTERNS:
            if pattern in name:
                score -= 0.30
        if len(Path(c.path).parts) > 9:      # deeply buried means disorganised
            score -= 0.10
        return round(max(0.0, min(1.0, score)), 4)

    # ----------------- Aggregation -----------------

    def score(self, c: Candidate, peers: Sequence[Candidate]) -> tuple[float, dict[str, float]]:
        parts = {
            "format": self._score_format(c),
            "bitrate": self._score_bitrate(c, peers),
            "resolution": self._score_resolution(c),
            "tags": self._score_tags(c),
            "artwork": self._score_artwork(c),
            "lyrics": self._score_lyrics(c),
            "duration": self._score_duration(c, peers),
            "path": self._score_path(c),
        }
        total = sum(parts[k] * self.weights.get(k, 0.0) for k in parts)
        denom = sum(self.weights.get(k, 0.0) for k in parts) or 1.0
        return round(total / denom, 5), parts

    def decide(self, candidates: Iterable[Candidate]) -> Verdict | None:
        peers = list(candidates)
        if len(peers) < 2:
            return None

        scores: dict[int, float] = {}
        breakdowns: dict[int, dict[str, float]] = {}
        for c in peers:
            total, parts = self.score(c, peers)
            scores[c.track_id] = total
            breakdowns[c.track_id] = parts

        # Order by score, then size, then earliest discovery -- the last two
        # keys make the verdict stable across runs.
        ranked = sorted(
            peers,
            key=lambda c: (scores[c.track_id], c.size_bytes, -c.first_seen_ts),
            reverse=True,
        )
        winner, losers = ranked[0], ranked[1:]
        margin = scores[winner.track_id] - scores[losers[0].track_id]

        reasons = {winner.track_id: self._explain_keep(winner, breakdowns[winner.track_id])}
        for lo in losers:
            reasons[lo.track_id] = self._explain_drop(
                lo, winner, breakdowns[lo.track_id], breakdowns[winner.track_id])

        return Verdict(
            keep=winner, losers=losers, scores=scores, breakdowns=breakdowns,
            reasons=reasons, ambiguous=margin < self.AMBIGUITY_MARGIN,
        )

    # ----------------- Human-readable rationale -----------------

    @staticmethod
    def _explain_keep(c: Candidate, parts: dict[str, float]) -> str:
        bits = []
        if c.lossless:
            bits.append(f"lossless ({c.codec})")
        elif c.bitrate:
            bits.append(f"{c.bitrate // 1000} kbps")
        if parts.get("tags", 0) >= 0.8:
            bits.append("near-complete tags")
        if c.has_artwork and (c.artwork_px or 0) >= 600:
            bits.append(f"{c.artwork_px}px artwork")
        if c.has_synced_lyrics:
            bits.append("synced lyrics")
        return "Best copy: " + ", ".join(bits) if bits else "Highest score in cluster"

    @staticmethod
    def _explain_drop(lo: Candidate, win: Candidate,
                      lp: dict[str, float], wp: dict[str, float]) -> str:
        gaps = sorted(
            ((k, wp[k] - lp[k]) for k in wp if wp[k] - lp[k] > 0.05),
            key=lambda kv: kv[1], reverse=True,
        )[:2]
        labels = {
            "format": "lower-ranked format", "bitrate": "lower bitrate",
            "resolution": "lower resolution", "tags": "incomplete tags",
            "artwork": "missing or small artwork", "lyrics": "no lyrics",
            "duration": "shorter (possibly truncated)", "path": "messy location",
        }
        if not gaps:
            return "Equivalent copy -- kept the higher-ranked one"
        return "; ".join(labels.get(k, k) for k, _ in gaps)
