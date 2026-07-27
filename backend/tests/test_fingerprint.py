"""Acoustic fingerprint comparison and LSH bucketing."""
from __future__ import annotations

from app.core import fingerprint as fp
from tests.conftest import encode_fingerprint, reencode

THRESHOLD = 0.90     # matches the default acoustic_match_threshold


def test_identical_fingerprint_scores_one(rng):
    words = [rng.getrandbits(32) for _ in range(300)]
    fingerprint = encode_fingerprint(words)
    assert fp.similarity(fingerprint, fingerprint) >= 0.99


def test_reencode_stays_above_threshold(rng):
    words = [rng.getrandbits(32) for _ in range(300)]
    original = encode_fingerprint(words)
    transcoded = encode_fingerprint(reencode(words, rng, rate=0.5))
    assert fp.similarity(original, transcoded) >= THRESHOLD


def test_different_songs_stay_well_below_threshold(rng):
    a = encode_fingerprint([rng.getrandbits(32) for _ in range(300)])
    b = encode_fingerprint([rng.getrandbits(32) for _ in range(300)])
    score = fp.similarity(a, b)
    # Hamming agreement between unrelated fingerprints hovers around 0.5,
    # never near 0 -- the margin to the threshold is what matters.
    assert 0.40 < score < 0.65
    assert score < THRESHOLD - 0.25


def test_decode_roundtrip(rng):
    words = [rng.getrandbits(32) for _ in range(64)]
    assert (fp.decode(encode_fingerprint(words)) or [])[:len(words)] == words


def test_decode_rejects_garbage():
    assert fp.decode("") is None
    assert fp.decode("!!") is None


def test_bucket_keys_are_deterministic_and_banded(rng):
    fingerprint = encode_fingerprint([rng.getrandbits(32) for _ in range(300)])
    keys = fp.bucket_keys(fingerprint)
    assert keys == fp.bucket_keys(fingerprint)
    assert len(keys) > 1                      # multiple bands, not a single hash
    assert len(set(keys)) == len(keys)        # bands are distinctly labelled


def test_reencode_shares_at_least_one_band(rng):
    """The whole point of banding: a bit flip must not hide a true pair.

    A single-band scheme loses roughly half of these pairs.
    """
    misses = 0
    trials = 40
    for _ in range(trials):
        words = [rng.getrandbits(32) for _ in range(300)]
        a = set(fp.bucket_keys(encode_fingerprint(words)))
        b = set(fp.bucket_keys(encode_fingerprint(reencode(words, rng, rate=0.5))))
        if not (a & b):
            misses += 1
    assert misses == 0, f"{misses}/{trials} re-encodes fell into disjoint buckets"
