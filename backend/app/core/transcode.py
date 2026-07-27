"""Audio conversion engine (FFmpeg): modernise legacy formats, shrink large files.

Every conversion writes to a temporary file first and only replaces the target
on success, so an interrupted job can never leave a half-written track behind.
The source file is kept unless the caller explicitly opts out.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core import audio_probe
from app.core.tags import TagSet, extract_artwork, read_tags, write_tags

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Preset:
    name: str
    ext: str
    args: list[str]
    lossless: bool
    label: str
    description: str


PRESETS: dict[str, Preset] = {
    # --- Lossless targets ---
    "flac": Preset(
        "flac", ".flac", ["-c:a", "flac", "-compression_level", "8"], True,
        "FLAC (level 8)",
        "Lossless. Best archival choice; roughly 50-60% of WAV size."),
    "flac_16_44": Preset(
        "flac_16_44", ".flac",
        ["-c:a", "flac", "-compression_level", "8", "-sample_fmt", "s16", "-ar", "44100"],
        True, "FLAC 16-bit / 44.1 kHz",
        "Lossless at CD resolution. Cuts hi-res files by ~60% with no audible loss."),
    "alac": Preset(
        "alac", ".m4a", ["-c:a", "alac"], True, "ALAC",
        "Lossless in an Apple-native container. Use for iOS/macOS playback."),
    "wav": Preset(
        "wav", ".wav", ["-c:a", "pcm_s16le"], True, "WAV (PCM 16-bit)",
        "Uncompressed. Only for editing workflows; very large."),

    # --- Lossy targets ---
    "aac_256": Preset(
        "aac_256", ".m4a",
        ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"], False,
        "AAC 256 kbps", "Transparent for most listeners. Widest device support."),
    "aac_vbr_high": Preset(
        "aac_vbr_high", ".m4a",
        ["-c:a", "aac", "-q:a", "1.4", "-movflags", "+faststart"], False,
        "AAC VBR (high)", "Variable bitrate AAC; similar quality, smaller files."),
    "mp3_v0": Preset(
        "mp3_v0", ".mp3", ["-c:a", "libmp3lame", "-q:a", "0"], False,
        "MP3 V0 (VBR ~245 kbps)", "Highest-quality MP3. Use for legacy players."),
    "mp3_320": Preset(
        "mp3_320", ".mp3", ["-c:a", "libmp3lame", "-b:a", "320k"], False,
        "MP3 320 kbps (CBR)", "Constant bitrate MP3. Maximum compatibility."),
    "mp3_192": Preset(
        "mp3_192", ".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"], False,
        "MP3 192 kbps", "Space-saving MP3 for phones and car stereos."),
    "opus_160": Preset(
        "opus_160", ".opus", ["-c:a", "libopus", "-b:a", "160k", "-vbr", "on"], False,
        "Opus 160 kbps", "Most efficient codec by size/quality. Limited hardware support."),
    "opus_96": Preset(
        "opus_96", ".opus", ["-c:a", "libopus", "-b:a", "96k", "-vbr", "on"], False,
        "Opus 96 kbps", "Aggressive size reduction for streaming or mobile copies."),
    "ogg_q6": Preset(
        "ogg_q6", ".ogg", ["-c:a", "libvorbis", "-q:a", "6"], False,
        "Ogg Vorbis q6", "Open lossy format, ~192 kbps equivalent."),
}

# Codecs worth upgrading automatically.
LEGACY_CODECS = {"wmav1", "wmav2", "wmapro", "ra_144", "ra_288", "adpcm_ms", "amr_nb"}


@dataclass(slots=True)
class TranscodeResult:
    src: str
    dst: str | None
    ok: bool
    preset: str
    src_bytes: int = 0
    dst_bytes: int = 0
    error: str | None = None
    skipped_reason: str | None = None

    @property
    def saved_bytes(self) -> int:
        return max(0, self.src_bytes - self.dst_bytes) if self.ok else 0


class Transcoder:
    def __init__(self) -> None:
        self.s = get_settings()

    def suggest(self, codec: str | None, bitrate: int | None, lossless: bool,
                size_bytes: int, duration: float | None) -> str | None:
        """Recommend a preset for a file, or None if it is already fine."""
        c = (codec or "").lower()
        if c in LEGACY_CODECS:
            # WMA and friends: go lossless if the source was high bitrate,
            # otherwise a well-supported lossy target.
            return "flac" if (bitrate or 0) > 320_000 else "aac_256"
        # Oversized hi-res masters: offering 16/44 typically saves ~60%.
        if lossless and duration and duration > 0 and size_bytes * 8 / duration > 2_800_000:
            return "flac_16_44"
        return None

    def transcode(self, src: Path, preset_name: str, *,
                  dest_dir: Path | None = None, keep_original: bool = True,
                  dry_run: bool = False, timeout: int = 3600) -> TranscodeResult:
        preset = PRESETS.get(preset_name)
        if preset is None:
            return TranscodeResult(str(src), None, False, preset_name,
                                   error=f"unknown preset: {preset_name}")
        if not src.is_file():
            return TranscodeResult(str(src), None, False, preset_name, error="file not found")

        src_bytes = src.stat().st_size
        out_dir = dest_dir or src.parent
        dst = out_dir / (src.stem + preset.ext)
        if dst.resolve() == src.resolve():
            dst = out_dir / f"{src.stem}.converted{preset.ext}"

        if dry_run:
            return TranscodeResult(str(src), str(dst), True, preset_name,
                                   src_bytes=src_bytes,
                                   skipped_reason="dry run: nothing was written")

        if audio_probe.probe(src).corrupt:
            # Do not give up: -err_detect ignore_err often salvages a damaged file.
            log.info("attempting to salvage damaged file: %s", src.name)

        tmp = dst.with_suffix(dst.suffix + ".part")
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.s.ffmpeg_bin, "-y", "-nostdin", "-v", "error",
            "-err_detect", "ignore_err", "-i", str(src),
            "-map", "0:a:0", "-map_metadata", "0", "-vn",
            "-threads", str(self.s.transcode_threads),
            *preset.args, str(tmp),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            tmp.unlink(missing_ok=True)
            return TranscodeResult(str(src), None, False, preset_name,
                                   src_bytes=src_bytes, error="conversion timed out")

        if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return TranscodeResult(
                str(src), None, False, preset_name, src_bytes=src_bytes,
                error=res.stderr.decode("utf-8", "ignore")[:400] or "ffmpeg failed")

        tmp.replace(dst)

        # ffmpeg does not carry every tag across dissimilar containers, so we
        # rewrite them (and the cover) explicitly.
        try:
            self._carry_tags(src, dst)
        except Exception as exc:
            log.warning("could not copy tags to %s: %s", dst.name, exc)

        dst_bytes = dst.stat().st_size
        if not keep_original:
            try:
                src.unlink()
            except OSError as exc:
                log.warning("original not removed %s: %s", src, exc)

        return TranscodeResult(str(src), str(dst), True, preset_name,
                               src_bytes=src_bytes, dst_bytes=dst_bytes)

    @staticmethod
    def _carry_tags(src: Path, dst: Path) -> None:
        tags: TagSet = read_tags(src)
        art = extract_artwork(src)
        write_tags(dst, tags,
                   artwork=art[0] if art else None,
                   artwork_mime=art[1] if art else "image/jpeg")

    async def transcode_async(self, src: Path, preset: str, **kw) -> TranscodeResult:
        return await asyncio.to_thread(self.transcode, src, preset, **kw)

    async def batch(self, items: list[tuple[Path, str]], *, dry_run: bool = False,
                    keep_original: bool = True, dest_dir: Path | None = None,
                    concurrency: int | None = None) -> list[TranscodeResult]:
        # Half the worker count: ffmpeg is already multi-threaded, and the NAS
        # still has to serve files while this runs.
        sem = asyncio.Semaphore(concurrency or max(1, self.s.workers // 2))

        async def one(src: Path, preset: str) -> TranscodeResult:
            async with sem:
                return await self.transcode_async(
                    src, preset, dry_run=dry_run, keep_original=keep_original,
                    dest_dir=dest_dir)

        return list(await asyncio.gather(*(one(s, p) for s, p in items)))


def ffmpeg_available() -> bool:
    s = get_settings()
    return bool(shutil.which(s.ffmpeg_bin) and shutil.which(s.ffprobe_bin))
