"""Text normalisation, path sanitising and tag completeness."""
from __future__ import annotations

import pytest

from app.core.dedup import normalize_text
from app.core.organizer import sanitize
from app.core.tags import TagSet


@pytest.mark.parametrize("a,b", [
    # Arabic diacritics and tatweel must not affect matching
    ("أُغْنِيَةٌ", "اغنية"),
    # Hamza variants fold to bare alef
    ("إحساس", "احساس"),
    # Ta marbuta folds to ha
    ("ليلة", "ليله"),
    # Alef maqsura folds to ya
    ("عمرى", "عمري"),
])
def test_arabic_folding(a: str, b: str):
    assert normalize_text(a) == normalize_text(b)


@pytest.mark.parametrize("raw,expected", [
    ("Enta Omry (Official Video)", "enta omry"),
    ("Song feat. Someone", "song"),
    ("Song ft. Someone Else", "song"),
    ("Track [Remastered 2011]", "track"),
    ("Title (Radio Edit)", "title"),
    ("  Mixed   Spacing  ", "mixed spacing"),
])
def test_latin_noise_stripping(raw: str, expected: str):
    assert normalize_text(raw) == expected


def test_normalize_handles_empty():
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


@pytest.mark.parametrize("raw", ["AC/DC", "Song: Part 2", "What?", 'He said "hi"', "a\\b"])
def test_sanitize_removes_illegal_characters(raw: str):
    out = sanitize(raw)
    assert not set(out) & set('<>:"/\\|?*')


def test_sanitize_edge_cases():
    assert not sanitize("Album name. ").endswith((" ", "."))
    assert sanitize("CON") == "_CON"          # reserved on Windows/SMB clients
    assert sanitize("   ") == "Unknown"
    assert sanitize("أم كلثوم") == "أم كلثوم"


def test_sanitize_truncates_long_components():
    out = sanitize("x" * 500)
    assert len(out) <= 120


def test_tag_completeness_bounds():
    assert TagSet().completeness() == 0.0
    full = TagSet(title="t", artist="a", album="al", albumartist="aa", year=2001,
                  track_no=1, genre="g", disc_no=1, isrc="X", mb_recording_id="m")
    assert full.completeness() == 1.0
    assert 0.0 < TagSet(title="t", artist="a").completeness() < 1.0


def test_merge_missing_does_not_overwrite():
    merged = TagSet(title="original").merge_missing_from(
        TagSet(title="replacement", album="Some Album"))
    assert merged.title == "original"
    assert merged.album == "Some Album"
