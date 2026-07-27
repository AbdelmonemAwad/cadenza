"""Test fixtures. Every test runs against a throwaway config/database directory."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="cadenza-tests-"))
os.environ.setdefault("CADENZA_CONFIG_DIR", str(_tmp / "config"))
os.environ.setdefault("CADENZA_MUSIC_ROOT", str(_tmp / "music"))
os.environ.setdefault("CADENZA_QUARANTINE_ROOT", str(_tmp / "quarantine"))

import base64  # noqa: E402
import random  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def rng() -> random.Random:
    return random.Random(1337)


def encode_fingerprint(words: list[int]) -> str:
    """Pack 32-bit words the way fingerprint.decode() reads them back."""
    return base64.urlsafe_b64encode(
        b"".join(struct.pack(">I", w) for w in words)).decode().rstrip("=")


def reencode(words: list[int], rng: random.Random, rate: float = 0.4) -> list[int]:
    """Simulate a transcode: flip one random bit in `rate` of the words."""
    return [w ^ (1 << rng.randrange(32)) if rng.random() < rate else w for w in words]


@pytest.fixture
def candidate_factory():
    from app.core.decision import Candidate

    def make(**kw):
        defaults = {
            "sample_rate": 44100, "bit_depth": 16, "tag_completeness": 0.5,
            "has_artwork": False, "artwork_px": None,
            "has_lyrics": False, "has_synced_lyrics": False,
        }
        return Candidate(**{**defaults, **kw})

    return make
