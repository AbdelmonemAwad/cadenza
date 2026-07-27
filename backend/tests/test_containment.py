"""Path containment and the settings write policy (issues #6 and #7).

Each test states an escape that used to work. They are the regression net for
the two primitives that made an authenticated caller equivalent to a shell:
repointing the binaries the app executes, and reading or writing files outside
the library.
"""
from __future__ import annotations

import pytest

from app.core.paths import PathEscape, contained, is_contained
from app.core.settings_policy import (
    LOCKED_FIELDS,
    WRITABLE_FIELDS,
    SettingRejected,
    validate_patch,
)

# --------------------------------- contained ---------------------------------

def test_accepts_a_path_inside_the_root(tmp_path):
    inside = tmp_path / "artist" / "album" / "track.flac"
    assert contained(inside, tmp_path) == inside.resolve()


def test_accepts_the_root_itself(tmp_path):
    assert contained(tmp_path, tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize("escape", [
    "../outside.flac",
    "../../etc/passwd",
    "artist/../../outside.flac",
    "artist/./../../outside.flac",
])
def test_rejects_dot_dot_traversal(tmp_path, escape):
    with pytest.raises(PathEscape):
        contained(tmp_path / escape, tmp_path)


def test_rejects_an_absolute_path_elsewhere(tmp_path):
    with pytest.raises(PathEscape):
        contained("/etc/passwd", tmp_path)


def test_rejects_a_sibling_with_a_shared_prefix(tmp_path):
    """A textual startswith check passes /music-backup for root /music."""
    root = tmp_path / "music"
    root.mkdir()
    sibling = tmp_path / "music-backup" / "track.flac"
    with pytest.raises(PathEscape):
        contained(sibling, root)


def test_rejects_a_symlink_pointing_outside(tmp_path):
    """Resolution, not spelling: the path has no '..' in it at all."""
    import os

    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")

    link = root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")

    assert not is_contained(link / "secret.txt", root)


def test_does_not_require_the_path_to_exist(tmp_path):
    """Destinations are validated before they are created."""
    assert contained(tmp_path / "not" / "yet" / "there.flac", tmp_path)


# ------------------------------ settings policy ------------------------------

@pytest.mark.parametrize("field", [
    "ffmpeg_bin", "ffprobe_bin", "fpcalc_bin",     # argv[0] of every subprocess
    "music_root", "quarantine_root", "config_dir",  # relocate all file operations
    "apple_private_key_path",
])
def test_execution_and_path_settings_cannot_be_written_remotely(field):
    with pytest.raises(SettingRejected):
        validate_patch({field: "/bin/sh"})


def test_locked_and_writable_sets_do_not_overlap():
    assert not (LOCKED_FIELDS & set(WRITABLE_FIELDS)), \
        "a field cannot be both locked and writable"


def test_unknown_field_is_rejected():
    with pytest.raises(SettingRejected):
        validate_patch({"totally_made_up": 1})


@pytest.mark.parametrize("value", [
    "/config/settings.json",        # absolute: pathlib would swallow the parent
    "../../config/settings.json",
    "sub/cover.jpg",
    "..",
    "cover\x00.jpg",
])
def test_cover_filename_must_be_a_bare_filename(value):
    """This was the arbitrary file read: the artwork endpoint returned whatever
    this pointed at, labelled image/jpeg."""
    with pytest.raises(SettingRejected):
        validate_patch({"cover_filename": value})


def test_cover_filename_accepts_a_normal_name():
    assert validate_patch({"cover_filename": "folder.jpg"}) == {"cover_filename": "folder.jpg"}


@pytest.mark.parametrize("value", ["/", "\\", "../", "a/b"])
def test_replacement_character_cannot_introduce_a_separator(value):
    """sanitize() substitutes this into every path component."""
    with pytest.raises(SettingRejected):
        validate_patch({"replace_unsafe_with": value})


@pytest.mark.parametrize("value", [
    "/etc/{title}",
    "../{album}/{title}",
    "{album}/../../{title}",
])
def test_path_templates_cannot_escape(value):
    with pytest.raises(SettingRejected):
        validate_patch({"path_template": value})


def test_quarantine_retention_cannot_be_zero():
    """Zero would make every future quarantine immediately purgeable, removing
    the safety net the whole design rests on."""
    with pytest.raises(SettingRejected):
        validate_patch({"quarantine_retention_days": 0})
    assert validate_patch({"quarantine_retention_days": 30})


def test_acoustic_threshold_cannot_be_set_below_the_noise_floor():
    """Unrelated audio scores ~0.50, so a low threshold matches different songs."""
    with pytest.raises(SettingRejected):
        validate_patch({"acoustic_match_threshold": 0.2})
    assert validate_patch({"acoustic_match_threshold": 0.93})


def test_booleans_must_actually_be_booleans():
    with pytest.raises(SettingRejected):
        validate_patch({"hard_delete_allowed": "yes"})
    assert validate_patch({"hard_delete_allowed": True}) == {"hard_delete_allowed": True}


def test_safety_switches_remain_writable():
    """They are meant to be user-controlled; the point is validation, not lockout."""
    cleaned = validate_patch({"dry_run_default": False, "hard_delete_allowed": True})
    assert cleaned == {"dry_run_default": False, "hard_delete_allowed": True}


# ------------------------- End to end, over the real API -------------------------

_API_PASSWORD = "containment-test-password"


@pytest.fixture
def api(app_client):
    from app.core.auth import Credentials, hash_password, save_credentials

    save_credentials(Credentials(password_hash=hash_password(_API_PASSWORD),
                                 must_change=False))
    app_client.cookies.clear()
    assert app_client.post("/api/v1/auth/login",
                           json={"password": _API_PASSWORD}).status_code == 200
    return app_client


def test_api_refuses_to_repoint_an_executable(api):
    """The remote code execution half of issue #6.

    ffprobe_bin is argv[0] of a subprocess run during every scan.
    """
    response = api.patch("/api/v1/settings", json={"ffprobe_bin": "/bin/sh"})
    assert response.status_code == 400
    assert "ffprobe_bin" in response.json()["detail"]

    # And it really did not take effect.
    assert api.get("/api/v1/settings").json()["ffprobe_bin"] == "ffprobe"


def test_api_refuses_an_absolute_cover_filename(api):
    """The arbitrary file read half of issue #6."""
    response = api.patch("/api/v1/settings",
                         json={"cover_filename": "/config/settings.json"})
    assert response.status_code == 400
    assert api.get("/api/v1/settings").json()["cover_filename"] == "cover.jpg"


def test_api_refuses_to_relocate_the_library(api):
    assert api.patch("/api/v1/settings", json={"music_root": "/"}).status_code == 400


def test_scan_outside_the_library_is_rejected(api):
    """handle_scan used to index any absolute path into the database."""
    from app.config import get_settings
    from app.core.paths import PathEscape, contained

    with pytest.raises(PathEscape):
        contained("/etc", get_settings().music_root)


def test_convert_rejects_a_destination_outside_the_library(api):
    """Refused at the boundary, not after the job is already queued.

    The first version of this test accepted 503 as well, on the grounds that a
    runner without ffmpeg refuses everything anyway. That hid a real gap: on a
    runner that does have ffmpeg the request was accepted with a job id and a
    200, and the escape was only caught later, inside the job. The status code
    must not depend on whether ffmpeg happens to be installed.
    """
    response = api.post("/api/v1/convert", json={
        "preset": "flac", "dest_dir": "/etc/cadenza-out", "dry_run": True})
    assert response.status_code == 400
    assert "outside the library" in response.json()["detail"]
    assert "job_id" not in response.json()


def test_writable_endpoint_lists_the_policy(api):
    body = api.get("/api/v1/settings/writable").json()
    assert "dry_run_default" in body["writable"]
    assert "ffmpeg_bin" in body["locked"]
    assert not set(body["writable"]) & set(body["locked"])
