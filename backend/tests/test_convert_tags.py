"""Converting must not throw the metadata away.

Nothing exercised this before: the transcode tests never ran ffmpeg, and the
tag tests never converted. So `write_tags` not knowing what an Opus file is
reached a NAS, where converting to the preset Cadenza itself recommends as the
most efficient produced files with no title, no artist, no album and no cover.
The only trace was one line in a log — `could not copy tags to X.opus` — and
the conversion still reported success.

These tests generate a real audio file with ffmpeg, convert it through the real
transcoder, and read the tags back out of the result.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.tags import TagSet, read_tags, write_tags
from app.core.transcode import PRESETS, Transcoder, ffmpeg_available

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg is not installed on this runner")

TAGS = TagSet(title="Test Title", artist="Test Artist", albumartist="Test Album Artist",
              album="Test Album", year=1999, date="1999", genre="Test Genre",
              track_no=7, total_tracks=12)

# A 1x1 JPEG, so artwork is exercised without a fixture file.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffc00011080001000103012200021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5"
    "10000201030302040305050404000001780102030004110512213141061351610722711432"
    "8191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a8384858687"
    "88898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8"
    "c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00080101"
    "00003f00f7fa2803ffd9")


def _make_source(path: Path, seconds: float = 1.0) -> Path:
    """A real, decodable FLAC. Silence is enough: this is about metadata."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}", "-c:a", "flac", str(path)],
        check=True, capture_output=True, timeout=120)
    return path


@needs_ffmpeg
@pytest.mark.parametrize("preset", ["opus_96", "ogg_q6", "wav", "mp3_192",
                                    "aac_256", "flac", "alac"])
def test_tags_survive_every_container(tmp_path: Path, preset: str) -> None:
    source = _make_source(tmp_path / "source.flac")
    write_tags(source, TAGS, artwork=JPEG)

    result = Transcoder().transcode(source, preset, dest_dir=tmp_path,
                                    keep_original=True)
    assert result.ok, f"{preset} failed: {result.error}"
    assert result.dst

    got = read_tags(Path(result.dst))
    assert got.title == TAGS.title, f"{preset} lost the title"
    assert got.artist == TAGS.artist, f"{preset} lost the artist"
    assert got.album == TAGS.album, f"{preset} lost the album"


@needs_ffmpeg
@pytest.mark.parametrize("preset", ["opus_96", "ogg_q6", "flac", "mp3_192", "aac_256"])
def test_artwork_survives_the_formats_that_can_carry_it(tmp_path: Path,
                                                        preset: str) -> None:
    """Ogg containers hold the cover in a base64 comment, not a picture block.

    `_write_vorbis` embedded it only for FLAC, so it was dropped for Vorbis as
    well as for Opus -- silently, because the tags themselves were written and
    the call reported success.
    """
    source = _make_source(tmp_path / "art-source.flac")
    write_tags(source, TAGS, artwork=JPEG)

    result = Transcoder().transcode(source, preset, dest_dir=tmp_path,
                                    keep_original=True)
    assert result.ok, f"{preset} failed: {result.error}"

    got = read_tags(Path(result.dst))
    assert got.has_artwork, f"{preset} lost the cover"


@needs_ffmpeg
def test_writing_tags_to_opus_does_not_raise(tmp_path: Path) -> None:
    """The exact call that used to raise, on the exact container."""
    source = _make_source(tmp_path / "src.flac")
    result = Transcoder().transcode(source, "opus_96", dest_dir=tmp_path)
    assert result.ok
    write_tags(Path(result.dst), TAGS, artwork=JPEG)   # used to raise ValueError
    assert read_tags(Path(result.dst)).title == TAGS.title


@needs_ffmpeg
def test_every_preset_produces_audio_that_reads_back(tmp_path: Path) -> None:
    """A guard on the preset table itself.

    Each preset is a list of ffmpeg arguments; a typo in one of them produces a
    preset that fails only when a user picks it.
    """
    from app.core import audio_probe

    source = _make_source(tmp_path / "all.flac")
    failures = []
    for name in sorted(PRESETS):
        out = tmp_path / name
        out.mkdir()
        result = Transcoder().transcode(source, name, dest_dir=out)
        if not result.ok:
            failures.append(f"{name}: {result.error}")
            continue
        info = audio_probe.probe(Path(result.dst))
        if info.corrupt or not info.duration:
            failures.append(f"{name}: produced unreadable audio")
    assert not failures, "presets that do not work:\n  " + "\n  ".join(failures)
