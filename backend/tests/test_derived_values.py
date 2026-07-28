"""Values the application derives from others, and got wrong.

Each of these is a number or a flag computed from something else, where the
something else changed and the derived value did not — or was set from a
condition that did not mean what it looked like.
"""
from __future__ import annotations

import pytest

from app.core.quality import quality_score, score_for_track
from app.db.models import Track, TrackStatus
from app.providers.aggregator import MetadataAggregator
from app.providers.base import TrackMetadata
from app.services.job_runner import _outcome


def _track(**kw) -> Track:
    base = {"path": "/m/x.flac", "filename": "x.flac", "ext": ".flac",
            "size_bytes": 1, "status": TrackStatus.ACTIVE, "lossless": True,
            "bitrate": 900_000, "sample_rate": 44100, "bit_depth": 16,
            "tag_completeness": 0.5}
    base.update(kw)
    return Track(**base)


# ------------------------------ quality score ------------------------------

def test_the_score_moves_when_the_tags_do() -> None:
    """It is 20% tag completeness, so enrichment changes it by definition.

    Enrichment updated `tag_completeness` and left `quality_score` alone, so
    the dashboard's quality figure and every quality-sorted view kept showing
    the library as it was before enrichment ran — until a full rescan.
    """
    poor = score_for_track(_track(tag_completeness=0.2))
    rich = score_for_track(_track(tag_completeness=1.0))
    assert rich > poor, "improving the tags did not improve the score"
    assert round(rich - poor, 4) == 0.16      # 0.20 weight * 0.8 difference


def test_lossless_beats_a_high_bitrate_lossy_file() -> None:
    lossless = quality_score(lossless=True, bitrate=None, sample_rate=44100,
                             bit_depth=16, tag_completeness=0.5)
    lossy = quality_score(lossless=False, bitrate=320_000, sample_rate=44100,
                          bit_depth=16, tag_completeness=0.5)
    assert lossless >= lossy


def test_the_scanner_and_enrichment_use_the_same_formula() -> None:
    """Two copies of this drifted apart once already."""
    import inspect

    from app.core import scanner
    from app.services import enrichment

    assert "quality_score" in inspect.getsource(scanner)
    assert "score_for_track" in inspect.getsource(enrichment)


# --------------------------- year and date agree ---------------------------

def test_a_date_that_disagrees_with_the_voted_year_is_dropped() -> None:
    """They are voted independently and written to the same ID3 frame.

    `date` and `year` have separate trust maps — Discogs is trusted most for
    the pressing year and less for the full date, deliberately — so they can be
    decided from different sources. The file then gets `TDRC` from
    `date or year` and ends up with the other one: the user reads 1973 in
    Cadenza and 1972 in every player.
    """
    aggregator = MetadataAggregator()
    merged = aggregator.merge([
        TrackMetadata(source="discogs", title="Song", artist="A",
                      year=1973, confidence=0.9),
        TrackMetadata(source="musicbrainz", title="Song", artist="A",
                      date="1972-05-01", confidence=0.9),
    ])
    if merged.metadata.year and merged.metadata.date:
        assert str(merged.metadata.date)[:4] == str(merged.metadata.year), \
            "the file would be written with a different year than the one shown"


def test_a_date_that_agrees_is_kept() -> None:
    """Dropping the date whenever both exist would lose real precision."""
    aggregator = MetadataAggregator()
    merged = aggregator.merge([
        TrackMetadata(source="musicbrainz", title="Song", artist="A",
                      year=1972, date="1972-05-01", confidence=0.9),
    ])
    assert merged.metadata.date == "1972-05-01"


# ----------------------------- job outcome -----------------------------

@pytest.mark.parametrize("result,expected", [
    ({"added": 12, "errors": 2}, (12, 2)),
    ({"converted": 5, "failed": 1}, (5, 1)),
    ({"moved": 30, "failed": 0}, (30, 0)),
    ({"computed": 7, "failed": 3}, (7, 3)),
    ({"restored": ["a", "b"], "failed": []}, (2, 0)),
    ({}, (None, None)),
])
def test_the_generic_counters_are_derived_from_whatever_the_handler_reported(
        result, expected) -> None:
    """`succeeded` and `failed` were columns nothing ever wrote.

    They were returned by the API and rendered in the job result panel, so
    every job that had ever run reported 0 and 0 however much it had done.
    """
    assert _outcome(result) == expected


def test_a_boolean_is_not_mistaken_for_a_count() -> None:
    """`dry_run: True` must not be read as "1 succeeded"."""
    assert _outcome({"dry_run": True, "added": 4}) == (4, None)
