"""Serving the SPA from the application itself (the native package has no nginx).

The dangerous failure here is ordering: the SPA fallback matches any unclaimed
path, so if it is registered before the API routers it swallows every endpoint
and answers JSON requests with HTML. These tests build a real app with a real
bundle on disk and check that the API still wins.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web import mount_frontend


@pytest.fixture
def bundle(tmp_path):
    """A minimal Vite-shaped build output."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Cadenza</title>", encoding="utf-8")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.ico").write_bytes(b"\x00")
    return tmp_path


@pytest.fixture
def client(bundle):
    app = FastAPI()

    @app.get("/api/v1/tracks")
    async def tracks() -> dict:
        return {"items": []}

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    assert mount_frontend(app, bundle, "/api/v1")
    return TestClient(app)


def test_the_shell_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Cadenza" in r.text


def test_a_client_side_route_returns_the_shell(client):
    """Deep links have to work on refresh, which is the whole point of the
    fallback."""
    r = client.get("/duplicates")
    assert r.status_code == 200
    assert "Cadenza" in r.text


def test_a_real_asset_is_served_as_itself(client):
    r = client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_a_root_level_file_is_served_as_itself(client):
    assert client.get("/favicon.ico").status_code == 200


def test_the_api_is_not_swallowed(client):
    """The ordering defect, stated directly. If the fallback is registered
    first, this returns 200 with HTML instead of the endpoint's JSON."""
    r = client.get("/api/v1/tracks")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"items": []}


def test_health_is_not_swallowed(client):
    r = client.get("/health")
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"status": "ok"}


def test_an_unknown_api_route_stays_a_404(client):
    """It must not become the SPA shell: a client parsing HTML as JSON gets an
    error that says nothing about what actually went wrong."""
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert "<!doctype html" not in r.text.lower()


def test_the_shell_is_not_cached(client):
    """Otherwise a browser keeps an old bundle after an upgrade and talks to an
    API it no longer matches."""
    assert client.get("/").headers["cache-control"] == "no-store"


def test_hashed_assets_are_cached_hard(client):
    assert "immutable" in client.get("/assets/app-abc123.js").headers["cache-control"]


@pytest.mark.parametrize("escape", [
    "../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "assets/../../secret.txt",
])
def test_traversal_out_of_the_bundle_is_refused(client, bundle, escape):
    """The path comes from a URL, so it is resolved and checked against the
    bundle root rather than trusted."""
    outside = bundle.parent / "secret.txt"
    outside.write_text("do not serve me", encoding="utf-8")

    r = client.get(f"/{escape}")
    assert "do not serve me" not in r.text
    assert "root:" not in r.text


def test_mounting_without_a_bundle_is_refused_not_crashed(tmp_path):
    """A packaging mistake should leave a headless API, not an app that will
    not start."""
    app = FastAPI()
    assert mount_frontend(app, tmp_path, "/api/v1") is False


def test_the_container_path_is_unaffected():
    """www_dir is unset by default, so nginx keeps serving the bundle and the
    deployed image behaves exactly as before."""
    from app.config import Settings

    assert Settings().www_dir is None
