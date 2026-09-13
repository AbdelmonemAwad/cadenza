"""Technical audio properties via ffprobe (covers every supported container)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import LOSSLESS_CODECS, get_settings


@dataclass(slots=True)
class AudioInfo:
    codec: str | None = None
    profile: str | None = None
    duration: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    lossless: bool = False
    container: str | None = None
    corrupt: bool = False
    error: str | None = None
    raw_tags: dict[str, str] = field(default_factory=dict)


_BIT_DEPTH_MAP = {
    "s16": 16, "s16p": 16, "s32": 32, "s32p": 32,
    "u8": 8, "u8p": 8, "flt": 32, "fltp": 32, "dbl": 64, "dblp": 64,
}


class ProbeUnavailable(RuntimeError):
    """ffprobe itself cannot be run -- as opposed to a file it cannot read.

    The two used to collapse into one result. A missing binary came back as an
    AudioInfo with `error` set and `corrupt` left False, and the scanner reads
    `corrupt` alone -- so every file was indexed as healthy with no codec, no
    duration and no bitrate, the scan reported success, and the only trace of
    the real problem was a string on a dataclass that nothing displayed. A NAS
    whose ffprobe was broken -- a package upgrade that failed halfway, a wrong
    `CADENZA_FFPROBE_BIN` -- got a library that looked fine and held none of
    what a scan exists to find out.

    A verdict on the tool is not a verdict on the file, so it is raised, not
    returned, and the scan that depends on it fails with the reason.
    """


def check_available(timeout: int = 15) -> str:
    """Prove ffprobe can run before a scan depends on it thousands of times.

    Returns the first line of `ffprobe -version`. Raises ProbeUnavailable with
    the configured path and the reason it could not be run.
    """
    binary = get_settings().ffprobe_bin
    try:
        res = subprocess.run([binary, "-version"], capture_output=True,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProbeUnavailable(
            f"{binary} did not answer -version within {timeout}s") from exc
    except OSError as exc:
        raise ProbeUnavailable(_launch_failure(binary, exc)) from exc
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", "ignore").strip()[:200]
        raise ProbeUnavailable(
            f"{binary} -version exited {res.returncode}: {err or 'no output'}")
    lines = res.stdout.decode("utf-8", "ignore").splitlines()
    return lines[0] if lines else "ffprobe"


def _launch_failure(binary: str, exc: OSError) -> str:
    reason = exc.strerror or exc.__class__.__name__
    return (f"cannot run {binary}: {reason}. Check CADENZA_FFPROBE_BIN, or "
            f"reinstall the package if its bundled ffprobe is missing")


def probe(path: Path, timeout: int = 60) -> AudioInfo:
    s = get_settings()
    cmd = [
        s.ffprobe_bin, "-v", "error", "-hide_banner", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "a:0", str(path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return AudioInfo(corrupt=True, error="ffprobe timeout")
    except OSError as exc:
        # The launch failed -- binary missing, not executable, a directory --
        # which says nothing about the file, so it is not returned as if it did.
        raise ProbeUnavailable(_launch_failure(s.ffprobe_bin, exc)) from exc

    if res.returncode != 0:
        return AudioInfo(corrupt=True,
                         error=res.stderr.decode("utf-8", "ignore")[:400] or "ffprobe failed")
    try:
        data = json.loads(res.stdout or b"{}")
    except json.JSONDecodeError:
        return AudioInfo(corrupt=True, error="ffprobe returned invalid json")

    streams = data.get("streams") or []
    if not streams:
        return AudioInfo(corrupt=True, error="no audio stream")

    st = streams[0]
    fmt = data.get("format") or {}

    codec = (st.get("codec_name") or "").lower() or None
    bitrate = _to_int(st.get("bit_rate")) or _to_int(fmt.get("bit_rate"))
    duration = _to_float(st.get("duration")) or _to_float(fmt.get("duration"))

    bit_depth = _to_int(st.get("bits_per_raw_sample")) or _to_int(st.get("bits_per_sample")) or None
    if not bit_depth:
        bit_depth = _BIT_DEPTH_MAP.get((st.get("sample_fmt") or "").lower())

    info = AudioInfo(
        codec=codec,
        profile=st.get("profile"),
        duration=duration,
        bitrate=bitrate,
        sample_rate=_to_int(st.get("sample_rate")),
        bit_depth=bit_depth,
        channels=_to_int(st.get("channels")),
        lossless=codec in LOSSLESS_CODECS if codec else False,
        container=(fmt.get("format_name") or "").split(",")[0] or None,
        raw_tags={str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()},
    )

    # ALAC inside m4a is reported as codec "alac" by some builds and only via
    # the profile field by others.
    if codec == "alac" or (info.profile or "").lower() == "alac":
        info.lossless = True

    # Derive an approximate bitrate when the container does not carry one.
    if not info.bitrate and info.duration and info.duration > 0:
        with contextlib.suppress(OSError):
            info.bitrate = int(path.stat().st_size * 8 / info.duration)

    if not info.duration or info.duration <= 0:
        info.corrupt = True
        info.error = info.error or "zero/unknown duration"
    return info


def verify_integrity(path: Path, timeout: int = 900) -> tuple[bool, str | None]:
    """Full decode pass used by the corruption check."""
    s = get_settings()
    cmd = [s.ffmpeg_bin, "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, "decode timeout"
    err = res.stderr.decode("utf-8", "ignore").strip()
    return (res.returncode == 0 and not err), (err[:500] or None)


def _to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # guards against NaN
    except (TypeError, ValueError):
        return None


async def probe_async(path: Path) -> AudioInfo:
    return await asyncio.to_thread(probe, path)
