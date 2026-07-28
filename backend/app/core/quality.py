"""The single 0..1 quality figure, in one place.

It is a derived value: format, sample rate, bit depth and how complete the tags
are. Anything that changes one of those inputs has to recompute it, and the
scanner was the only thing that did — so enrichment updated `tag_completeness`
and left `quality_score` reflecting the tags as they were before it ran, for
ever, because nothing recomputes it until a full rescan.

Two callers, one formula. It used to be a static method on the scanner taking
the probe result, which is why enrichment could not call it and quietly did not.
"""
from __future__ import annotations

# Weights. Format dominates because a lossless file is the thing worth keeping;
# tags matter but a perfectly tagged 128 kbps MP3 is still a 128 kbps MP3.
FORMAT_WEIGHT = 0.55
SAMPLE_RATE_WEIGHT = 0.15
BIT_DEPTH_WEIGHT = 0.10
TAG_WEIGHT = 0.20

# 320 kbps is the top of the lossy range; anything above it scores the same as
# a lossless file on the format axis alone, which is why lossless is checked
# first rather than inferred from bitrate.
REFERENCE_BITRATE = 320_000
REFERENCE_SAMPLE_RATE = 96_000
REFERENCE_BIT_DEPTH = 24


def quality_score(*, lossless: bool, bitrate: int | None,
                  sample_rate: int | None, bit_depth: int | None,
                  tag_completeness: float) -> float:
    fmt = 1.0 if lossless else min((bitrate or 0) / REFERENCE_BITRATE, 1.0)
    rate = min((sample_rate or 44100) / REFERENCE_SAMPLE_RATE, 1.0)
    depth = min((bit_depth or 16) / REFERENCE_BIT_DEPTH, 1.0)
    return round(FORMAT_WEIGHT * fmt
                 + SAMPLE_RATE_WEIGHT * rate
                 + BIT_DEPTH_WEIGHT * depth
                 + TAG_WEIGHT * (tag_completeness or 0.0), 4)


def score_for_track(track) -> float:
    """Convenience for callers holding a Track row rather than a probe result."""
    return quality_score(
        lossless=bool(track.lossless), bitrate=track.bitrate,
        sample_rate=track.sample_rate, bit_depth=track.bit_depth,
        tag_completeness=track.tag_completeness or 0.0)
