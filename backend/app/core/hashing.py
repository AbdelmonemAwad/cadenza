"""File fingerprints: SHA256 over the container, MD5 over the audio stream alone.

The distinction matters. Two files holding identical audio but different tags
or cover art produce different SHA256 values and the same audio MD5 -- and that
is the single most common real-world duplicate.
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

from app.config import get_settings


def sha256_file(path: Path, chunk: int | None = None) -> str:
    s = get_settings()
    chunk = chunk or s.hash_chunk_bytes
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def quick_signature(path: Path, head_tail: int = 256 * 1024) -> str:
    """Cheap pre-filter: size plus the first and last 256 KB.

    On large libraries this removes over 90% of full-file SHA256 work, because
    only files sharing a quick signature need the expensive pass.
    """
    size = path.stat().st_size
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with path.open("rb") as fh:
        h.update(fh.read(head_tail))
        if size > head_tail * 2:
            fh.seek(-head_tail, 2)
            h.update(fh.read(head_tail))
    return h.hexdigest()


def audio_stream_md5(path: Path, timeout: int = 300) -> str | None:
    """MD5 of the decoded samples only -- no tags, artwork or container padding.

    ffmpeg normalises to a fixed PCM format, so a FLAC file matches a WAV
    exported from the same master. This does not replace acoustic
    fingerprinting: any re-encode (MP3 vs FLAC) changes the samples, which is
    what fingerprint.py is for.
    """
    s = get_settings()
    cmd = [
        s.ffmpeg_bin, "-v", "error", "-nostdin", "-i", str(path),
        "-map", "0:a:0", "-f", "md5", "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.decode("ascii", "ignore").strip()
    return text.split("=", 1)[1] if text.startswith("MD5=") else None


async def sha256_file_async(path: Path) -> str:
    return await asyncio.to_thread(sha256_file, path)


async def audio_stream_md5_async(path: Path) -> str | None:
    return await asyncio.to_thread(audio_stream_md5, path)
