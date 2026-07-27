"""Regressions for the paths that could destroy a user's only copy (issue #8).

Every test here corresponds to a concrete way the app used to lose a file. The
project's central promise is that nothing is ever deleted outright -- removals
go to quarantine and can be restored -- and each of these was a hole in it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.organizer import Organizer
from app.core.quarantine import _free_name
from app.core.transcode import Transcoder, _unique_sibling, ffmpeg_available

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg is not installed on this runner")


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ------------------------- Conversion: the destination -------------------------

def test_unique_sibling_skips_names_that_are_taken(tmp_path):
    _touch(tmp_path / "song.flac")
    _touch(tmp_path / "song (2).flac")
    assert _unique_sibling(tmp_path / "song.flac") == tmp_path / "song (3).flac"


def test_unique_sibling_gives_up_rather_than_returning_a_used_path(tmp_path):
    """None means 'no free name'. Returning a taken path would have the caller
    write straight over it."""
    _touch(tmp_path / "song.flac")
    for i in range(2, 6):
        _touch(tmp_path / f"song ({i}).flac")
    assert _unique_sibling(tmp_path / "song.flac", limit=5) is None


def test_conversion_does_not_target_an_existing_file(tmp_path):
    """Converting a.wav to FLAC in a folder that already holds a.flac used to
    overwrite it -- possibly the lossless original the job was producing."""
    source = _touch(tmp_path / "a.wav")
    victim = _touch(tmp_path / "a.flac", b"the original lossless copy")

    result = Transcoder().transcode(source, "flac", dry_run=True)

    assert result.ok
    assert Path(result.dst) != victim
    assert victim.read_bytes() == b"the original lossless copy"


@needs_ffmpeg
def test_conversion_never_deletes_the_source_itself(tmp_path):
    """keep_original=false used to call src.unlink() inside the transcoder:
    no quarantine, no hard_delete_allowed check, no audit entry. The transcoder
    now only reports that the source is ready to be replaced; the job routes it
    through quarantine."""
    import subprocess

    from app.config import get_settings

    source = tmp_path / "tone.wav"
    subprocess.run(
        [get_settings().ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(source)],
        check=True, capture_output=True)

    result = Transcoder().transcode(source, "flac", keep_original=False)

    assert result.ok
    assert source.is_file(), "the transcoder deleted the source"
    assert result.source_replaced is True


# --------------------------- Organiser: move targets ---------------------------

def test_dedupe_target_raises_instead_of_returning_an_occupied_path(tmp_path):
    """It used to break out of the loop and return the last candidate even
    though the file existed; apply() then moved a different track on top of
    it."""
    base = tmp_path / "track.flac"
    _touch(base)
    for i in range(2, 101):
        _touch(tmp_path / f"track ({i}).flac")

    with pytest.raises(FileExistsError):
        Organizer._dedupe_target(base, set())


def test_dedupe_target_returns_a_free_name_when_one_exists(tmp_path):
    _touch(tmp_path / "track.flac")
    assert Organizer._dedupe_target(tmp_path / "track.flac", set()) == \
        tmp_path / "track (2).flac"


def test_dedupe_target_respects_names_claimed_earlier_in_the_same_plan(tmp_path):
    claimed = {str(tmp_path / "track.flac").lower()}
    assert Organizer._dedupe_target(tmp_path / "track.flac", claimed) == \
        tmp_path / "track (2).flac"


# ------------------------------ Quarantine naming ------------------------------

def test_free_name_finds_the_first_unused_variant(tmp_path):
    _touch(tmp_path / "song__restored.flac")
    assert _free_name(tmp_path / "song.flac", "__restored") == \
        tmp_path / "song__restored_2.flac"


def test_free_name_returns_none_when_every_variant_is_taken(tmp_path):
    _touch(tmp_path / "song__restored.flac")
    for i in range(2, 6):
        _touch(tmp_path / f"song__restored_{i}.flac")
    assert _free_name(tmp_path / "song.flac", "__restored", limit=5) is None


def test_free_name_does_not_accumulate_markers(tmp_path):
    """The old quarantine loop rebuilt each candidate from the previous one and
    produced names like track__1__2__3."""
    _touch(tmp_path / "track__dup.flac")
    result = _free_name(tmp_path / "track.flac", "__dup")
    assert result == tmp_path / "track__dup_2.flac"
    assert "__dup__dup" not in result.name


# ---------------------------- Purge cannot be forced ----------------------------

def test_purge_expired_takes_no_force_argument():
    """`POST /quarantine/purge?force=true` skipped hard_delete_allowed entirely.
    The parameter is gone, so no caller can reintroduce it."""
    import inspect

    from app.core.quarantine import QuarantineManager

    assert "force" not in inspect.signature(QuarantineManager.purge_expired).parameters


def test_purge_api_ignores_a_force_query_parameter(app_client):
    """Belt and braces: even spelled out in the URL it has no effect, and the
    request stays a dry run because that is the default."""
    from app.core.auth import Credentials, hash_password, save_credentials

    password = "purge-test-password"
    save_credentials(Credentials(password_hash=hash_password(password),
                                 must_change=False))
    app_client.cookies.clear()
    assert app_client.post("/api/v1/auth/login",
                           json={"password": password}).status_code == 200

    response = app_client.post("/api/v1/quarantine/purge?force=true")
    assert response.status_code == 200
    assert response.json()["dry_run"] is True


async def test_purge_is_blocked_while_hard_delete_is_off():
    """The setting is now the only switch, so it has to actually stop the delete.

    No session is passed on purpose: the guard has to run before any query, and
    a None here proves it does. If the check ever moves below the SELECT this
    test fails with an AttributeError rather than quietly passing.
    """
    from app.config import get_settings
    from app.core.quarantine import QuarantineManager

    assert get_settings().hard_delete_allowed is False
    manager = QuarantineManager(None)  # type: ignore[arg-type]
    assert await manager.purge_expired() == {"purged": 0, "skipped": 0, "blocked": 1}
