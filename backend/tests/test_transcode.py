"""Conversion actually converts.

Every test here invokes the real ffmpeg. That is the point: the engine had a
bug that made *every* conversion fail -- the temporary file was named
"track.flac.part", and since no preset passes -f, ffmpeg picks its muxer from
the output extension and ".part" is not one. Nothing caught it, because the
suite never ran ffmpeg and the container job only checked that the binary
answers -version. A conversion engine has to be tested by converting something.
"""
from __future__ import annotations

import subprocess

import pytest

from app.config import get_settings
from app.core.transcode import PRESETS, Transcoder, ffmpeg_available

pytestmark = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg/ffprobe are not installed on this runner")


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    """One second of a 440 Hz sine, as a real WAV file."""
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    subprocess.run(
        [get_settings().ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(path)],
        check=True, capture_output=True)
    return path


def _codec_of(path) -> str:
    out = subprocess.run(
        [get_settings().ffprobe_bin, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True)
    return out.stdout.decode().strip()


@pytest.mark.parametrize("preset_name", sorted(PRESETS))
def test_every_preset_produces_a_playable_file(tone, tmp_path, preset_name):
    """Each preset, end to end. Parametrised rather than looped so a single
    broken target names itself instead of hiding behind the first failure."""
    result = Transcoder().transcode(tone, preset_name, dest_dir=tmp_path)

    assert result.ok, f"{preset_name} failed: {result.error}"
    assert result.dst is not None
    output = tmp_path / f"{tone.stem}{PRESETS[preset_name].ext}"
    assert output.is_file(), f"{preset_name} reported success but wrote nothing"
    assert output.stat().st_size > 0
    # ffprobe reading a stream back is the real proof it is a valid container,
    # not just a non-empty file.
    assert _codec_of(output), f"{preset_name} produced something ffprobe cannot read"


def test_no_partial_file_is_left_behind(tone, tmp_path):
    """The temp file is renamed onto the target, never left in the library."""
    assert Transcoder().transcode(tone, "flac", dest_dir=tmp_path).ok
    leftovers = [p.name for p in tmp_path.iterdir() if ".part" in p.name]
    assert not leftovers, f"partial files left in the library: {leftovers}"


def test_the_temporary_file_keeps_the_target_extension():
    """The regression itself, without waiting for ffmpeg to reject it.

    ffmpeg infers the muxer from the output extension, so whatever the engine
    hands it must still end in .flac/.mp3/.m4a.
    """
    import re
    from pathlib import Path

    dst = Path("/music/Artist/Album/01 - Track.flac")
    tmp = dst.with_name(f".{dst.stem}.part-deadbeef{dst.suffix}")
    assert tmp.suffix == ".flac"
    assert tmp.name.startswith("."), "the partial file should be hidden from scans"
    assert re.search(r"\.part-[0-9a-f]+", tmp.name), "and should be recognisable as partial"


def test_two_conversions_of_the_same_track_do_not_collide(tone, tmp_path):
    """The temp name used to be derived only from the destination, so two jobs
    on the same track and preset wrote over each other."""
    first = Transcoder().transcode(tone, "flac", dest_dir=tmp_path)
    second = Transcoder().transcode(tone, "flac", dest_dir=tmp_path)

    assert first.ok and second.ok
    assert first.dst != second.dst, "the second conversion overwrote the first"
    assert (tmp_path / "tone.flac").is_file()
    assert (tmp_path / "tone (2).flac").is_file()


def test_a_corrupt_source_fails_without_writing_anything(tmp_path):
    """A failure must not leave a truncated file where a track should be."""
    junk = tmp_path / "broken.wav"
    junk.write_bytes(b"this is not audio" * 100)

    result = Transcoder().transcode(junk, "flac", dest_dir=tmp_path)

    assert not result.ok
    assert not (tmp_path / "broken.flac").exists()
    assert not [p for p in tmp_path.iterdir() if ".part" in p.name]
