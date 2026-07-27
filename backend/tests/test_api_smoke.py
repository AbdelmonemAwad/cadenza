"""Smoke test: the app boots, routes are wired, and read endpoints answer.

This is the check that catches import cycles, bad router prefixes and schema
mistakes that unit tests on the engines would never see.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import Credentials, hash_password, save_credentials
from app.main import create_app


@pytest.fixture(scope="module")
def client():
    """An authenticated client.

    Every /api/v1 route now requires a session, so the smoke tests sign in
    once. The unauthenticated behaviour is asserted in test_auth.py instead of
    being re-tested here.
    """
    with TestClient(create_app()) as c:
        save_credentials(Credentials(password_hash=hash_password(_PASSWORD),
                                     must_change=False))
        response = c.post("/api/v1/auth/login", json={"password": _PASSWORD})
        assert response.status_code == 200, response.text
        yield c


_PASSWORD = "smoke-test-password-value"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["app"] == "Cadenza"


def test_openapi_schema_builds():
    """The schema must still be constructible even though it is not served.

    It is disabled in production because it maps every destructive endpoint
    (issue #5), so this builds it directly rather than over HTTP.
    """
    schema = create_app().openapi()
    assert schema["info"]["title"] == "Cadenza"
    assert any(p.startswith("/api/v1/convert") for p in schema["paths"])


@pytest.mark.parametrize("path", [
    "/api/v1/dashboard/stats",
    "/api/v1/library/tracks",
    "/api/v1/library/albums",
    "/api/v1/library/codecs",
    "/api/v1/duplicates/groups",
    "/api/v1/duplicates/report",
    "/api/v1/quarantine",
    "/api/v1/quarantine/stats",
    "/api/v1/jobs",
    "/api/v1/jobs/kinds",
    "/api/v1/jobs/schedule/tasks",
    "/api/v1/convert/presets",
    "/api/v1/convert/codecs",
    "/api/v1/convert/candidates",
    "/api/v1/settings",
    "/api/v1/settings/health",
    "/api/v1/apple/status",
])
def test_read_endpoints_respond(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_every_job_kind_is_registered(client):
    kinds = client.get("/api/v1/jobs/kinds").json()
    assert {"scan", "dedup_analyze", "dedup_apply", "enrich",
            "organize", "convert", "quarantine_purge"} <= set(kinds)


def test_conversion_presets_are_described(client):
    data = client.get("/api/v1/convert/presets").json()
    assert data["lossless"] and data["lossy"]
    for preset in data["lossless"] + data["lossy"]:
        assert preset["label"] and preset["description"] and preset["ext"].startswith(".")


def test_unknown_preset_is_rejected(client):
    r = client.post("/api/v1/convert", json={"preset": "not-a-codec"})
    assert r.status_code in (400, 503)


def test_secrets_are_never_returned(client):
    body = client.get("/api/v1/settings").json()
    for field in ("acoustid_api_key", "discogs_token", "lastfm_api_key"):
        assert isinstance(body[field], bool), f"{field} must be masked to a boolean"


def test_settings_rejects_unknown_field(client):
    r = client.patch("/api/v1/settings", json={"totally_made_up": 1})
    assert r.status_code == 400


def test_settings_roundtrip(client):
    assert client.patch("/api/v1/settings",
                        json={"artwork_min_px": 700}).status_code == 200
    assert client.get("/api/v1/settings").json()["artwork_min_px"] == 700
    client.patch("/api/v1/settings", json={"artwork_min_px": 600})


def test_safety_defaults_are_conservative(client):
    safety = client.get("/api/v1/settings/health").json()["safety"]
    assert safety["dry_run_default"] is True
    assert safety["hard_delete_allowed"] is False
