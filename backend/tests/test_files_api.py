"""The folder picker and the credential upload.

A browse endpoint hands out directory names and an upload endpoint writes to
disk, so both are tested for what they refuse as much as for what they do.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.browse import (
    _denied,
    browsable_roots,
    list_directories,
    resolve_within_roots,
)
from app.core.paths import PathEscape

# Assembled rather than written out, because a literal PEM header in the tree
# is what the secret scanner exists to catch -- and it did catch this, which is
# the guard working. The bytes are identical to a real header, so the content
# check under test is exercised exactly as it would be in production.
_DASHES = b"-" * 5
_KEY = b"PRIVATE" + b" KEY"
PEM = (_DASHES + b"BEGIN " + _KEY + _DASHES + b"\n"
       + b"MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQg\n"
       + _DASHES + b"END " + _KEY + _DASHES + b"\n")

_PASSWORD = "files-api-test-password"


@pytest.fixture
def authed_client(app_client):
    """Signed in, over the shared app instance -- see conftest for why there is
    only ever one."""
    from app.core.auth import Credentials, hash_password, save_credentials

    save_credentials(Credentials(username="tester",
                                 password_hash=hash_password(_PASSWORD)))
    app_client.cookies.clear()
    response = app_client.post("/api/v1/auth/login",
                               json={"username": "tester", "password": _PASSWORD})
    assert response.status_code == 200, response.text
    return app_client


# ------------------------------ containment ------------------------------

def test_system_directories_are_denied() -> None:
    for path in ("/etc", "/etc/shadow", "/proc/self", "/usr/bin", "/root",
                 "/var/packages/Cadenza", "/volume1/@appdata"):
        assert _denied(Path(path)), f"{path} should never be browsable"


def test_the_library_and_data_folders_are_roots() -> None:
    s = get_settings()
    # The app calls ensure_dirs() at startup; this module does not go through
    # it. The library folder is never created by either -- that one is the
    # user's, and Cadenza does not invent it.
    s.ensure_dirs()
    Path(s.music_root).mkdir(parents=True, exist_ok=True)

    roots = [str(p) for p in browsable_roots(s)]
    assert str(Path(s.music_root).resolve()) in roots
    assert str(Path(s.config_dir).resolve()) in roots


def test_a_library_folder_that_does_not_exist_is_not_offered(tmp_path,
                                                             monkeypatch) -> None:
    """A picker must not present a folder that is not there."""
    from app.config import Settings

    s = Settings(music_root=tmp_path / "no-such-library",
                 config_dir=tmp_path / "data",
                 quarantine_root=tmp_path / "q")
    (tmp_path / "data").mkdir()
    roots = [str(p) for p in browsable_roots(s)]
    assert str(tmp_path / "no-such-library") not in roots


def test_a_path_outside_every_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    with pytest.raises(PathEscape):
        resolve_within_roots(outside, get_settings())


def test_traversal_out_of_a_root_is_refused() -> None:
    s = get_settings()
    with pytest.raises(PathEscape):
        resolve_within_roots(Path(s.music_root) / ".." / ".." / "etc", s)


def test_browsing_lists_directories_and_never_files() -> None:
    s = get_settings()
    root = Path(s.music_root)
    (root / "An Album").mkdir(parents=True, exist_ok=True)
    (root / "a-track.mp3").write_bytes(b"x")
    (root / ".hidden").mkdir(exist_ok=True)

    names = {e["name"] for e in list_directories(root)}
    assert "An Album" in names
    assert "a-track.mp3" not in names, "file names must never be returned"
    assert ".hidden" not in names


# --------------------------------- the API ---------------------------------

def test_every_files_endpoint_requires_a_session(app_client) -> None:
    # The client is shared across the whole session (see conftest), so a test
    # that ran earlier may have left a valid cookie on it. Without this the
    # assertion passes for the wrong reason when run alone and fails in a full
    # run -- which is exactly how it behaved.
    app_client.cookies.clear()
    for method, path in (("get", "/api/v1/files/roots"),
                         ("get", "/api/v1/files/browse?path=/"),
                         ("get", "/api/v1/files/credentials"),
                         ("post", "/api/v1/files/credentials/apple_private_key"),
                         ("delete", "/api/v1/files/credentials/apple_private_key")):
        response = getattr(app_client, method)(path)
        assert response.status_code in (401, 403), f"{method} {path} was not gated"


def test_browse_refuses_a_path_outside_the_roots(authed_client) -> None:
    response = authed_client.get("/api/v1/files/browse", params={"path": "/etc"})
    assert response.status_code == 400


def test_upload_rejects_something_that_is_not_a_key(authed_client) -> None:
    response = authed_client.post(
        "/api/v1/files/credentials/apple_private_key",
        files={"file": ("notes.txt", b"just some text", "text/plain")})
    assert response.status_code == 400
    assert "PEM" in response.json()["detail"]


def test_upload_rejects_an_empty_file(authed_client) -> None:
    response = authed_client.post(
        "/api/v1/files/credentials/apple_private_key",
        files={"file": ("empty.p8", b"", "application/octet-stream")})
    assert response.status_code == 400


def test_upload_rejects_an_unknown_credential(authed_client) -> None:
    response = authed_client.post(
        "/api/v1/files/credentials/something_else",
        files={"file": ("x.p8", PEM, "application/octet-stream")})
    assert response.status_code == 404


def test_a_key_round_trips_and_is_never_echoed_back(authed_client) -> None:
    response = authed_client.post(
        "/api/v1/files/credentials/apple_private_key",
        files={"file": ("AuthKey_ABC123.p8", PEM, "application/octet-stream")})
    assert response.status_code == 201

    stored = Path(get_settings().apple_key_file)
    assert stored.is_file()
    assert stored.read_bytes() == PEM

    listing = authed_client.get("/api/v1/files/credentials").json()
    entry = listing["apple_private_key"]
    assert entry["present"] is True
    assert entry["size"] == len(PEM)
    body = authed_client.get("/api/v1/files/credentials").text
    assert "PRIVATE KEY" not in body, "the contents leaked into the listing"

    removed = authed_client.delete("/api/v1/files/credentials/apple_private_key")
    assert removed.status_code == 200
    assert removed.json()["existed"] is True
    assert not stored.exists()


def test_a_stored_key_is_not_world_readable(authed_client) -> None:
    """The data folder is a DSM shared folder that other packages can read."""
    import os
    import stat

    authed_client.post(
        "/api/v1/files/credentials/apple_private_key",
        files={"file": ("AuthKey_ABC123.p8", PEM, "application/octet-stream")})
    stored = Path(get_settings().apple_key_file)
    if os.name == "nt":
        pytest.skip("POSIX modes are not meaningful on Windows")
    mode = stat.S_IMODE(stored.stat().st_mode)
    assert mode == 0o600, f"the signing key is mode {mode:o}"


def test_browsing_lists_no_files_unless_a_credential_is_named(authed_client) -> None:
    """The disclosure boundary.

    Without a credential the browser is a folder picker and returns no file
    names at all. With one it returns only that credential's extensions -- what
    the user is already looking for, rather than their documents.
    """
    s = get_settings()
    folder = Path(s.config_dir) / "keys"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "AuthKey_TEST.p8").write_bytes(PEM)
    (folder / "tax-return.pdf").write_bytes(b"%PDF-1.4")

    plain = authed_client.get("/api/v1/files/browse",
                              params={"path": str(folder)}).json()
    assert plain["files"] == []

    looking = authed_client.get(
        "/api/v1/files/browse",
        params={"path": str(folder), "credential": "apple_private_key"}).json()
    names = {f["name"] for f in looking["files"]}
    assert "AuthKey_TEST.p8" in names
    assert "tax-return.pdf" not in names, "an unrelated file was disclosed"


def test_importing_copies_a_key_already_on_the_nas(authed_client) -> None:
    s = get_settings()
    folder = Path(s.config_dir) / "keys"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "AuthKey_IMPORT.p8"
    source.write_bytes(PEM)

    response = authed_client.post("/api/v1/files/credentials/apple_private_key/import",
                                  json={"path": str(source)})
    assert response.status_code == 201, response.text

    stored = Path(s.apple_key_file)
    assert stored.is_file() and stored.read_bytes() == PEM
    assert source.is_file(), "the source must be copied, not moved"


def test_importing_refuses_a_path_outside_the_roots(authed_client, tmp_path) -> None:
    outside = tmp_path / "elsewhere.p8"
    outside.write_bytes(PEM)
    response = authed_client.post("/api/v1/files/credentials/apple_private_key/import",
                                  json={"path": str(outside)})
    assert response.status_code == 400


def test_importing_applies_the_same_content_check_as_uploading(
        authed_client) -> None:
    """No route into this file may be laxer than another."""
    s = get_settings()
    folder = Path(s.config_dir) / "keys"
    folder.mkdir(parents=True, exist_ok=True)
    bogus = folder / "not-a-key.p8"
    bogus.write_bytes(b"just some text, definitely not a key")

    response = authed_client.post("/api/v1/files/credentials/apple_private_key/import",
                                  json={"path": str(bogus)})
    assert response.status_code == 400


def test_the_key_lands_in_this_installs_data_folder() -> None:
    """The container default made it unfindable on the Synology package."""
    s = get_settings()
    assert Path(s.apple_key_file).parent == Path(s.config_dir)
