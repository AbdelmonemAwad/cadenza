"""Authentication: password storage, session tokens, and endpoint gating.

The gating tests matter most. This application moves and quarantines files the
user cannot re-download, so an endpoint accidentally left ungated is a
data-destruction hole, not just an information leak.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.auth import (
    Credentials,
    hash_password,
    issue_session,
    load_credentials,
    save_credentials,
    verify_password,
    verify_session,
)
from app.main import create_app

PASSWORD = "a-sufficiently-long-test-password"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def known_password():
    """Replace whatever first-run generated with a password the tests know."""
    previous = load_credentials()
    save_credentials(Credentials(password_hash=hash_password(PASSWORD), must_change=False))
    yield PASSWORD
    if previous is not None:
        save_credentials(previous)


# ------------------------------ Password hashing ------------------------------

def test_hash_is_salted_and_verifies():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b, "each hash must use a fresh salt"
    assert verify_password(PASSWORD, a)
    assert verify_password(PASSWORD, b)


def test_wrong_password_rejected():
    assert not verify_password("not the password", hash_password(PASSWORD))


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$bad", "md5$1$1$1$aa$bb"])
def test_malformed_hash_rejected_without_raising(stored):
    # A corrupted auth.json must fail closed, not crash the sign-in endpoint.
    assert verify_password(PASSWORD, stored) is False


# -------------------------------- Session tokens --------------------------------

def test_session_roundtrip():
    assert verify_session(issue_session()) == "admin"


@pytest.mark.parametrize("token", [None, "", "no-dot", "a.b", "...."])
def test_malformed_tokens_rejected(token):
    assert verify_session(token) is None


def test_tampered_payload_rejected():
    body, _, signature = issue_session().partition(".")
    assert verify_session(f"{body}x.{signature}") is None


def test_tampered_signature_rejected():
    body, _, signature = issue_session().partition(".")
    assert verify_session(f"{body}.{signature[:-4]}AAAA") is None


def test_expired_token_rejected():
    assert verify_session(issue_session(ttl=-1)) is None


# ------------------------------ Endpoint gating ------------------------------

# Every destructive or information-disclosing route. If a new one is added
# without a session check, it belongs here and this test should catch it.
PROTECTED = [
    ("get", "/api/v1/dashboard/stats"),
    ("get", "/api/v1/library/tracks"),
    ("get", "/api/v1/settings"),
    ("get", "/api/v1/settings/health"),
    ("get", "/api/v1/duplicates/groups"),
    ("get", "/api/v1/quarantine"),
    ("get", "/api/v1/jobs"),
    ("get", "/api/v1/convert/presets"),
    ("get", "/api/v1/apple/status"),
    ("get", "/api/v1/apple/developer-token"),
    ("post", "/api/v1/jobs"),
    ("post", "/api/v1/duplicates/apply"),
    ("post", "/api/v1/metadata/organize"),
    ("post", "/api/v1/convert"),
    ("post", "/api/v1/quarantine/purge"),
    ("patch", "/api/v1/settings"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_requires_authentication(client, method, path):
    client.cookies.clear()
    call = getattr(client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code == 401, (
        f"{method.upper()} {path} answered {response.status_code} without a session"
    )


def test_health_stays_public(client):
    client.cookies.clear()
    assert client.get("/health").status_code == 200


def test_auth_status_stays_public(client):
    client.cookies.clear()
    body = client.get("/api/v1/auth/status").json()
    # Must NOT disclose whether the generated password is still in force: that
    # would tell an unauthenticated caller the box is on its factory credential.
    assert set(body) == {"configured"}


def test_openapi_not_exposed_by_default(client):
    # The schema is a map of every destructive endpoint; it stays off unless
    # explicitly enabled for development.
    assert client.get("/api/openapi.json").status_code == 404
    assert client.get("/api/docs").status_code == 404


# --------------------------------- Sign-in flow ---------------------------------

def test_login_rejects_wrong_password(client, known_password):
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401


def test_login_then_access(client, known_password):
    client.cookies.clear()
    assert client.post("/api/v1/auth/login",
                       json={"password": known_password}).status_code == 200
    assert client.get("/api/v1/dashboard/stats").status_code == 200


def test_session_cookie_is_hardened(client, known_password):
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={"password": known_password})
    header = response.headers.get("set-cookie", "").lower()
    # HttpOnly keeps XSS from lifting the session; SameSite=Strict is what
    # actually blocks the cross-site requests described in issue #5.
    assert "httponly" in header
    assert "samesite=strict" in header


def test_logout_revokes_access(client, known_password):
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"password": known_password})
    assert client.get("/api/v1/dashboard/stats").status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/dashboard/stats").status_code == 401


def test_bearer_token_accepted_for_cli_use(client, known_password):
    client.cookies.clear()
    response = client.get("/api/v1/dashboard/stats",
                          headers={"Authorization": f"Bearer {issue_session()}"})
    assert response.status_code == 200


def test_password_change_requires_current(client, known_password):
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"password": known_password})
    response = client.post("/api/v1/auth/password", json={
        "current_password": "wrong", "new_password": "another-long-password"})
    assert response.status_code == 401


def test_password_change_rejects_reuse(client, known_password):
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"password": known_password})
    response = client.post("/api/v1/auth/password", json={
        "current_password": known_password, "new_password": known_password})
    assert response.status_code == 400


def test_password_change_enforces_minimum_length(client, known_password):
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"password": known_password})
    response = client.post("/api/v1/auth/password", json={
        "current_password": known_password, "new_password": "short"})
    assert response.status_code == 422       # rejected by the request model


def test_websocket_rejects_unauthenticated(client):
    from starlette.websockets import WebSocketDisconnect

    client.cookies.clear()
    # A router-level HTTP dependency does not gate a WebSocket route, so this
    # asserts the explicit check inside the handler.
    connect = client.websocket_connect("/api/v1/jobs/stream")
    with pytest.raises(WebSocketDisconnect), connect as ws:
        ws.receive_text()


# ---------------------- Hardening from the adversarial review ----------------------

def test_signing_key_survives_a_roundtrip_through_disk(tmp_path, monkeypatch):
    """Regression: the key was stored raw and read back with .strip().

    secrets.token_bytes can begin or end with 0x09/0x0a/0x0d/0x20, so roughly
    one key in twenty was mangled on reload and the first session issued by
    those installs could never be verified. It is stored hex-encoded now.
    """
    from app.core import auth as auth_mod

    settings = get_settings()
    monkeypatch.setattr(settings, "config_dir", tmp_path, raising=False)

    first = auth_mod._secret_key()
    assert auth_mod._secret_key() == first, "key must be stable across reads"

    # Force the exact byte pattern that used to break: 2 + 44 + 2 = 48 bytes.
    planted = b"\n\t" + b"\x11" * 44 + b" \r"
    (tmp_path / auth_mod.SECRET_FILE).write_text(planted.hex(), encoding="ascii")
    reloaded = auth_mod._secret_key()
    assert reloaded == planted, "whitespace bytes must survive the roundtrip"


def test_private_files_are_not_group_or_world_readable(tmp_path, monkeypatch):
    import os
    import stat

    from app.core import auth as auth_mod

    if not hasattr(os, "fchmod"):
        pytest.skip("POSIX permissions only")

    monkeypatch.setattr(get_settings(), "config_dir", tmp_path, raising=False)
    target = tmp_path / "secret-thing"
    auth_mod._write_private(target, b"sensitive")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_write_private_ignores_a_hostile_preexisting_temp_file(tmp_path, monkeypatch):
    """O_EXCL on a unique name: a pre-created temp file must not be reused,
    which would silently inherit its permissions."""
    from app.core import auth as auth_mod

    monkeypatch.setattr(get_settings(), "config_dir", tmp_path, raising=False)
    target = tmp_path / "thing"
    (tmp_path / ".thing.tmp").write_text("planted", encoding="utf-8")
    auth_mod._write_private(target, b"real")
    assert target.read_bytes() == b"real"


def test_password_change_revokes_other_sessions(client, known_password):
    """The epoch bump is what actually ends other sessions."""
    from app.core.auth import set_password

    other = issue_session()
    assert verify_session(other) is not None

    set_password("a-brand-new-long-password")
    try:
        assert verify_session(other) is None, "old session survived a password change"
    finally:
        save_credentials(Credentials(password_hash=hash_password(known_password),
                                     must_change=False))


def test_logout_everywhere_invalidates_issued_tokens(known_password):
    from app.core.auth import revoke_all_sessions

    token = issue_session()
    assert verify_session(token) is not None
    revoke_all_sessions()
    assert verify_session(token) is None


def test_first_run_session_cannot_reach_protected_routes(client):
    """A session issued while the generated password stands is scoped to the
    password endpoints, so the forced change cannot be skipped by refreshing."""
    from app.core.auth import SUBJECT_PASSWORD_RESET

    save_credentials(Credentials(password_hash=hash_password(PASSWORD),
                                 must_change=True))
    client.cookies.clear()
    login = client.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    assert verify_session(client.cookies.get("cadenza_session")) == SUBJECT_PASSWORD_RESET

    # Everything else refuses it.
    assert client.get("/api/v1/dashboard/stats").status_code == 403
    assert client.post("/api/v1/jobs", json={"kind": "scan"}).status_code == 403

    # But the password endpoint is reachable, which is the point.
    changed = client.post("/api/v1/auth/password", json={
        "current_password": PASSWORD, "new_password": "a-fresh-long-password"})
    assert changed.status_code == 200
    assert client.get("/api/v1/dashboard/stats").status_code == 200


def test_initial_password_file_is_removed_once_changed(tmp_path, monkeypatch):
    from app.core import auth as auth_mod

    monkeypatch.setattr(get_settings(), "config_dir", tmp_path, raising=False)
    generated = auth_mod.ensure_initialised()
    note = tmp_path / auth_mod.INITIAL_PASSWORD_FILE
    assert generated and note.is_file()

    auth_mod.set_password("some-other-long-password")
    assert not note.exists(), "the generated credential must not linger on disk"


def test_stale_cookie_does_not_suppress_a_valid_bearer_token(client, known_password):
    """Cookie and header are tried independently.

    Checking the cookie and giving up would lock the CLI out whenever an old
    browser cookie happened to be present.
    """
    client.cookies.clear()
    client.cookies.set("cadenza_session", "stale.garbage")
    response = client.get("/api/v1/dashboard/stats",
                          headers={"Authorization": f"Bearer {issue_session()}"})
    assert response.status_code == 200


@pytest.mark.parametrize("stored", [
    "scrypt$99999999$8$1$YWJj$ZGVm",     # absurd N would exhaust memory/CPU
    "scrypt$32768$9999$1$YWJj$ZGVm",     # absurd r
])
def test_out_of_range_stored_cost_is_rejected(stored):
    """A tampered auth.json must not turn each login into a DoS primitive."""
    assert verify_password(PASSWORD, stored) is False
