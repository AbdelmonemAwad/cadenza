"""A track nothing could identify is an outcome, not a failure.

The first enrichment to complete on a real library reported
`applied 66 · failed 234`, and every one of the 234 was the same line:
"confidence too low; nothing was changed". No provider had a confident match,
which is the expected answer for most of a badly-tagged library -- and it was
counted, coloured and read as 234 things gone wrong (issue #68).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config import get_settings
from app.db.base import SessionFactory, init_db
from app.db.models import Track, TrackStatus
from app.providers.aggregator import MergedResult
from app.services import job_runner
from app.services.enrichment import EnrichmentService
from app.services.job_runner import _outcome, handle_enrich, runner

pytestmark = pytest.mark.asyncio


class _LowConfidenceAggregator:
    async def identify(self, **_kw) -> MergedResult:
        return MergedResult(overall_confidence=0.31)

    async def aclose(self) -> None:
        return None


async def test_a_low_confidence_lookup_is_unmatched_not_an_error(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"\x00" * 1024)
    track = Track(id=1, path=str(song), filename="song.mp3", ext=".mp3",
                  status=TrackStatus.ACTIVE, size_bytes=1024)

    service = EnrichmentService(None, aggregator=_LowConfidenceAggregator())  # type: ignore[arg-type]
    result = await service.enrich(track, dry_run=True, min_confidence=0.55)

    assert result.unmatched is True
    assert result.error is None, "an unmatched lookup was reported as an error"
    assert result.applied is False and result.changed_fields == {}
    assert "0.31" in (result.reason or "") and "0.55" in (result.reason or "")
    assert result.confidence == 0.31, "the best confidence seen must be kept for the user"


@dataclass
class _Outcome:
    track_id: int
    path: str
    confidence: float = 0.0
    applied: bool = False
    changed_fields: dict = field(default_factory=dict)
    field_sources: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    artwork: None = None
    lyrics: None = None
    error: str | None = None
    unmatched: bool = False
    reason: str | None = None


async def test_the_job_counts_unmatched_apart_from_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """One applied, two unmatched, one real error: the job must say so in
    those words, and only the real error reaches the `failed` column."""
    await init_db()
    async with SessionFactory() as s:
        tracks = [Track(path=f"/music/outcome-{i}.mp3", filename=f"outcome-{i}.mp3", ext=".mp3",
                        status=TrackStatus.ACTIVE, size_bytes=1000, tag_completeness=0.1)
                  for i in range(4)]
        s.add_all(tracks)
        await s.commit()
        ids = [t.id for t in tracks]

    plan = {ids[0]: {"applied": True, "confidence": 0.9, "changed_fields": {"year": (None, 1999)}},
            ids[1]: {"unmatched": True, "reason": "no confident match (0.20 < 0.55)"},
            ids[2]: {"unmatched": True, "reason": "no confident match (0.00 < 0.55)"},
            ids[3]: {"error": "lookup failed: musicbrainz HTTP 503"}}

    class FakeService:
        def __init__(self, session, **_kw):
            self.session = session

        async def enrich(self, track, **_kw):
            return _Outcome(track_id=track.id, path=track.path, **plan[track.id])

        async def aclose(self):
            return None

    monkeypatch.setattr(job_runner, "EnrichmentService", FakeService)
    monkeypatch.setattr(get_settings(), "acoustid_api_key", "")
    out = await handle_enrich(0, {"track_ids": ids, "limit": 10}, True, runner)

    assert (out["applied"], out["unmatched"], out["skipped"], out["failed"]) == (1, 2, 0, 1)
    assert [i["reason"] for i in out["items"] if i["unmatched"]] == \
        ["no confident match (0.20 < 0.55)", "no confident match (0.00 < 0.55)"]
    assert "AcoustID" in out.get("hint", ""), \
        "most tracks unmatched and no AcoustID key: the result should say what would help"
    # What the job row records as failed: the real error alone.
    assert _outcome(out) == (1, 1)

    monkeypatch.setattr(get_settings(), "acoustid_api_key", "present")
    out = await handle_enrich(0, {"track_ids": ids, "limit": 10}, True, runner)
    assert "hint" not in out, "the hint only makes sense while there is no AcoustID key"
