"""Credential files and log redaction (issue #9).

The config volume is a DSM shared folder. Every file in it that holds a
credential has to be private from the moment it exists, and no credential may
reach cadenza.log, which sits in that same folder across five rotated copies.
"""
from __future__ import annotations

import logging
import os
import stat

import pytest

from app.core.secretfile import SECRET_MODE, tighten, write_private, write_private_text
from app.logging_conf import MIN_REDACTABLE, REDACTED, RedactingFormatter

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="file modes are not enforced on this platform")


# ------------------------------- Writing files -------------------------------

def test_write_private_writes_the_content(tmp_path):
    target = tmp_path / "secret.json"
    write_private_text(target, '{"token": "abc"}')
    assert target.read_text("utf-8") == '{"token": "abc"}'


@posix_only
def test_a_new_file_is_private_immediately(tmp_path):
    target = tmp_path / "secret.json"
    write_private(target, b"credential")
    assert stat.S_IMODE(target.stat().st_mode) == SECRET_MODE


@posix_only
def test_a_permissive_umask_does_not_widen_the_file(tmp_path):
    """The reason for fchmod: the open mode is masked by the umask, and a NAS
    shared folder commonly runs a permissive one."""
    target = tmp_path / "secret.json"
    old = os.umask(0)
    try:
        write_private(target, b"credential")
    finally:
        os.umask(old)
    assert stat.S_IMODE(target.stat().st_mode) == SECRET_MODE


def test_overwriting_replaces_the_content_entirely(tmp_path):
    """os.replace, not a truncate-and-write: a reader sees the old file or the
    new one, never a half-written credential."""
    target = tmp_path / "secret.json"
    write_private_text(target, "a-much-longer-original-value")
    write_private_text(target, "short")
    assert target.read_text("utf-8") == "short"


def test_no_temporary_file_is_left_behind(tmp_path):
    write_private_text(tmp_path / "secret.json", "value")
    assert [p.name for p in tmp_path.iterdir()] == ["secret.json"]


def test_a_planted_temp_file_is_refused_rather_than_reused(tmp_path, monkeypatch):
    """O_EXCL, tested against the name write_private will actually use.

    The first version of this test planted ".thing.tmp" -- a name the function
    can never generate, since it includes the pid and a random suffix. os.open
    was therefore never asked to open the planted file, and the test would have
    passed with O_EXCL removed entirely. The token is pinned here so the
    attacker's file and the target collide for real.
    """
    monkeypatch.setattr("app.core.secretfile.secrets.token_hex", lambda _n: "aaaaaaaa")
    target = tmp_path / "thing"
    planted = tmp_path / f".thing.{os.getpid()}.aaaaaaaa.tmp"
    planted.write_text("planted", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_private(target, b"real")

    # The planted file is untouched and the target was never created from it.
    assert planted.read_text(encoding="utf-8") == "planted"
    assert not target.exists()


def test_a_failed_write_leaves_no_orphan_holding_the_secret(tmp_path):
    """A partial write used to leave the credential under a random name.

    Nothing in the app could ever find it again: DELETE /apple/link, the
    removal of initial-password.txt and tighten_secret_files all match literal
    filenames. The secret would sit on the shared folder permanently, hidden
    from File Station's default view by its leading dot.
    """
    from unittest.mock import patch

    target = tmp_path / "apple_user_token.json"
    with patch("os.write", side_effect=OSError(28, "No space left on device")), \
            pytest.raises(OSError):
        write_private(target, b"the-music-user-token")

    assert list(tmp_path.iterdir()) == [], "a credential was left behind"


@posix_only
def test_overwriting_an_existing_permissive_file_still_ends_private(tmp_path):
    """O_EXCL on a temp name is what makes this work: opening the target
    directly with a mode argument would leave an existing file's mode alone."""
    target = tmp_path / "secret.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)
    write_private_text(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == SECRET_MODE


@posix_only
def test_tighten_fixes_a_file_written_by_an_earlier_version(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o644)
    tighten(target)
    assert stat.S_IMODE(target.stat().st_mode) == SECRET_MODE


def test_tighten_is_quiet_about_a_missing_file(tmp_path):
    tighten(tmp_path / "not-there.json")     # must not raise


# ------------------------------ Log redaction ------------------------------

@pytest.fixture
def secret_key(monkeypatch) -> str:
    """Install a provider key that redaction should catch."""
    from app.config import get_settings

    value = "acoustid-key-that-must-never-be-logged"
    monkeypatch.setattr(get_settings(), "acoustid_api_key", value, raising=False)
    return value


def _line(formatter: logging.Formatter, message: str, exc: BaseException | None = None) -> str:
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, message, (), None)
    if exc is not None:
        record.exc_info = (type(exc), exc, exc.__traceback__)
    return formatter.format(record)


def test_a_key_in_a_message_is_redacted(secret_key):
    formatter = RedactingFormatter("%(message)s")
    line = _line(formatter, f"GET https://api.acoustid.org/?client={secret_key}")
    assert secret_key not in line
    assert REDACTED in line


def test_a_key_inside_a_traceback_is_redacted(secret_key):
    """This is the path that matters: the URL arrives in an exception the job
    runner logs with exc_info, not in a message anyone wrote deliberately."""
    formatter = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"connect failed for https://ws.audioscrobbler.com/?api_key={secret_key}")
    except RuntimeError as exc:
        line = _line(formatter, "provider call failed", exc)
    assert secret_key not in line
    assert REDACTED in line


def test_ordinary_lines_are_untouched(secret_key):
    formatter = RedactingFormatter("%(message)s")
    assert _line(formatter, "scan finished: 1200 tracks") == "scan finished: 1200 tracks"


def test_an_empty_key_does_not_redact_everything(monkeypatch):
    """An unset provider key is "", and replacing "" would corrupt every line."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "acoustid_api_key", "", raising=False)
    monkeypatch.setattr(get_settings(), "lastfm_api_key", "", raising=False)
    monkeypatch.setattr(get_settings(), "discogs_token", "", raising=False)
    formatter = RedactingFormatter("%(message)s")
    assert _line(formatter, "scan finished") == "scan finished"


def test_a_short_value_is_not_used_as_a_needle(monkeypatch):
    """A placeholder like "test" appears inside ordinary words; redacting it
    would shred the log while protecting nothing."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "acoustid_api_key", "test", raising=False)
    formatter = RedactingFormatter("%(message)s")
    assert _line(formatter, "the latest scan finished") == "the latest scan finished"
    assert len("test") < MIN_REDACTABLE


# --------------------------- The Apple developer token ---------------------------

def test_the_developer_token_is_short_lived():
    """It is signed with the user's Apple Developer private key and cannot be
    revoked on its own; recovery from a leak means rotating that key in the
    developer portal. It used to be minted with a 150-day lifetime."""
    from app.providers.applemusic import TOKEN_REFRESH_MARGIN, TOKEN_TTL

    assert TOKEN_TTL <= 60 * 60 * 24, "a developer token must not outlive a day"
    assert 0 < TOKEN_REFRESH_MARGIN < TOKEN_TTL, \
        "the refresh margin has to leave the cache usable"


def test_the_developer_token_endpoint_requires_a_session(app_client):
    app_client.cookies.clear()
    assert app_client.get("/api/v1/apple/developer-token").status_code == 401


def test_linking_an_apple_account_requires_a_session(app_client):
    """Unauthenticated, this wrote a stranger's Music-User-Token into the config
    and redirected playlist import and export to their library."""
    app_client.cookies.clear()
    assert app_client.post("/api/v1/apple/link",
                           json={"music_user_token": "attacker"}).status_code == 401
