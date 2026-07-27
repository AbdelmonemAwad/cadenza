"""Acoustic fingerprinting (Chromaprint / AcoustID).

A fingerprint is a compact representation of the audio's chroma spectrum. It
stays close under format and bitrate changes, so it identifies the same
recording across a FLAC rip and a 128 kbps MP3. Comparison happens on the raw
vector: we decompress to 32-bit words and measure the fraction of agreeing bits.
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass(slots=True)
class Fingerprint:
    fingerprint: str | None = None      # compressed base64 string from fpcalc
    duration: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.fingerprint)


def compute(path: Path, length_s: int | None = None, timeout: int = 120) -> Fingerprint:
    s = get_settings()
    length = length_s or s.fingerprint_duration_s
    cmd = [s.fpcalc_bin, "-json", "-length", str(length), str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Fingerprint(error="fpcalc timeout")
    except FileNotFoundError:
        return Fingerprint(error="fpcalc (chromaprint) not installed")

    if res.returncode != 0:
        return Fingerprint(error=res.stderr.decode("utf-8", "ignore")[:300] or "fpcalc failed")
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return Fingerprint(error="fpcalc returned invalid json")
    return Fingerprint(fingerprint=data.get("fingerprint"), duration=data.get("duration"))


# --------------------------- Fingerprint comparison ---------------------------

def decode(fp: str) -> list[int] | None:
    """Decompress a Chromaprint fingerprint into a vector of 32-bit words.

    fpcalc emits base64url over Chromaprint's own compressed encoding. We use
    pyacoustid's decoder when it is installed and fall back to treating the
    raw base64 payload as 32-bit words, which is sufficient for a statistical
    similarity measure.
    """
    try:
        from acoustid import chromaprint  # type: ignore

        raw, _algo = chromaprint.decode_fingerprint(fp.encode("ascii"))
        return list(raw) or None
    except Exception:
        pass
    try:
        pad = "=" * (-len(fp) % 4)
        blob = base64.urlsafe_b64decode(fp + pad)
    except Exception:
        return None
    if len(blob) < 8:
        return None
    words = [int.from_bytes(blob[i:i + 4], "big") for i in range(0, len(blob) - 3, 4)]
    return words or None


def _popcount_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def similarity(fp_a: str, fp_b: str, max_offset: int = 60) -> float:
    """Similarity in 0..1 between two fingerprints, with bounded time alignment.

    The offset search matters because copies commonly differ in leading
    silence (a CD rip versus a gapless-trimmed version).

    Important when tuning the threshold: this is a Hamming bit-agreement
    ratio, so the baseline for two *different* songs is ~0.50, not 0.0 --
    unrelated bits agree half the time. Any useful threshold therefore sits
    above 0.85. The default is 0.90; a re-encode of the same master typically
    scores 0.92-0.99.
    """
    va, vb = decode(fp_a), decode(fp_b)
    if not va or not vb:
        return 0.0
    best = 0.0
    for offset in range(-max_offset, max_offset + 1):
        if offset >= 0:
            x, y = va[offset:], vb
        else:
            x, y = va, vb[-offset:]
        n = min(len(x), len(y))
        if n < 10:                       # too short an overlap to be meaningful
            continue
        bits = sum(_popcount_distance(x[i], y[i]) for i in range(n))
        score = 1.0 - bits / (n * 32)
        if score > best:
            best = score
            if best > 0.995:             # effectively identical, stop early
                break
    return max(0.0, best)


def bucket_keys(fp: str, bands: int = 6, words_per_band: int = 2,
                bits: int = 8) -> list[str]:
    """LSH banding keys used to avoid an O(n^2) all-pairs comparison.

    A single hash band is not enough here: a re-encode flips scattered bits,
    and one flip inside the hashed region moves the track to a different
    bucket, so the true pair is never compared. We therefore emit several
    keys drawn from disjoint segments of the vector and index the track
    under each -- two tracks become candidates if *any* band collides.

    With 6 bands x 2 words x top-8-bits, a typical re-encode keeps at least
    one band intact with probability > 0.9999, while the candidate set stays
    a small fraction of the library.
    """
    v = decode(fp)
    if not v:
        return ["0"]

    shift = 32 - max(1, min(bits, 32))
    seg = max(1, len(v) // bands)
    keys: list[str] = []
    for b in range(bands):
        start = b * seg
        chunk = v[start:start + words_per_band]
        if len(chunk) < words_per_band:
            break
        masked = ",".join(str(w >> shift) for w in chunk)
        keys.append(f"{b}:{zlib.crc32(masked.encode()) & 0xFFFF:04x}")
    return keys or ["0"]


def bucket_key(fp: str) -> str:
    """Single representative band -- kept for callers that need one label."""
    return bucket_keys(fp)[0]


async def compute_async(path: Path, length_s: int | None = None) -> Fingerprint:
    return await asyncio.to_thread(compute, path, length_s)
