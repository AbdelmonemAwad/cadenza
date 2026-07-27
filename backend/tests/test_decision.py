"""Decision engine: which duplicate copy survives."""
from __future__ import annotations

from app.core.decision import DEFAULT_WEIGHTS, DecisionEngine


def test_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_lossless_beats_lossy(candidate_factory):
    flac = candidate_factory(
        track_id=1, path="/music/Artist/2001 - Album/01 - Song.flac",
        size_bytes=40_000_000, codec="flac", lossless=True, bitrate=950_000,
        duration=240.0, tag_completeness=0.95, has_artwork=True, artwork_px=1400,
        has_lyrics=True, has_synced_lyrics=True, mb_recording_id="mbid-1")
    mp3_192 = candidate_factory(
        track_id=2, path="/music/Downloads/New folder/Song (1).mp3",
        size_bytes=7_000_000, codec="mp3", bitrate=192_000, duration=239.5,
        tag_completeness=0.40)
    mp3_320 = candidate_factory(
        track_id=3, path="/music/Artist/Album/Song.mp3",
        size_bytes=9_600_000, codec="mp3", bitrate=320_000, duration=240.0,
        tag_completeness=0.80, has_artwork=True, artwork_px=600)

    verdict = DecisionEngine().decide([mp3_192, flac, mp3_320])

    assert verdict is not None
    assert verdict.keep.track_id == 1
    assert len(verdict.losers) == 2
    assert verdict.ambiguous is False
    assert all(verdict.reasons.get(i) for i in (1, 2, 3))
    assert all(0.0 <= s <= 1.0 for s in verdict.scores.values())
    # Higher bitrate wins among the lossy copies
    assert verdict.scores[3] > verdict.scores[2]
    # The "New folder / (1)" copy is penalised on location
    assert verdict.breakdowns[2]["path"] < verdict.breakdowns[3]["path"]


def test_equivalent_copies_are_ambiguous(candidate_factory):
    common = {
        "size_bytes": 40_000_000, "codec": "flac", "lossless": True,
        "bitrate": 950_000, "duration": 240.0, "tag_completeness": 0.9,
        "has_artwork": True, "artwork_px": 1000,
    }
    a = candidate_factory(track_id=10, path="/m/a.flac", **common)
    b = candidate_factory(track_id=11, path="/m/b.flac", **common)

    verdict = DecisionEngine().decide([a, b])

    assert verdict is not None
    # Too close to call: the UI must ask rather than auto-quarantine.
    assert verdict.ambiguous is True


def test_truncated_copy_loses(candidate_factory):
    common = {
        "codec": "mp3", "bitrate": 320_000, "tag_completeness": 0.8,
        "has_artwork": True, "artwork_px": 800,
    }
    full = candidate_factory(track_id=20, path="/m/full.mp3",
                             size_bytes=9_000_000, duration=300.0, **common)
    cut = candidate_factory(track_id=21, path="/m/cut.mp3",
                            size_bytes=3_000_000, duration=95.0, **common)

    verdict = DecisionEngine().decide([full, cut])

    assert verdict is not None
    assert verdict.keep.track_id == 20


def test_single_candidate_yields_no_verdict(candidate_factory):
    only = candidate_factory(track_id=1, path="/m/a.flac", codec="flac", lossless=True)
    assert DecisionEngine().decide([only]) is None


def test_prefer_lossless_can_be_disabled(candidate_factory):
    flac = candidate_factory(track_id=1, path="/m/a.flac", codec="flac",
                             lossless=True, duration=200.0, size_bytes=40_000_000)
    engine = DecisionEngine(prefer_lossless=False)
    assert engine._score_format(flac) <= 0.70
