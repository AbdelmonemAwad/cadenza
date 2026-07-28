"""Usage statistics, and serving the application's own log.

A log viewer is an attractive target: the log is *expected* to contain paths,
so an endpoint that takes a filename is an arbitrary file read wearing a hat.
This one takes no path at all, and these tests hold it to that.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.api.v1.statistics import _tail
from app.config import get_settings
from app.core.auth import Credentials, hash_password, save_credentials
from app.db.base import SessionFactory, init_db
from app.db.models import AuditLog, Job, JobState, Track, TrackStatus

_PASSWORD = "statistics-test-password"


@pytest.fixture
async def client(app_client):
    await init_db()
    save_credentials(Credentials(username="stats",
                                 password_hash=hash_password(_PASSWORD)))
    app_client.cookies.clear()
    r = app_client.post("/api/v1/auth/login",
                        json={"username": "stats", "password": _PASSWORD})
    assert r.status_code == 200, r.text

    # Seeded once. The fixture is function-scoped, because pytest-asyncio gives
    # fixtures a function-scoped loop here, and Track.path is unique -- so
    # re-inserting on every test raises IntegrityError rather than the thing
    # under test.
    async with SessionFactory() as s:
        from sqlalchemy import select
        already = (await s.execute(
            select(Track).where(Track.path == "/stats/one.flac"))).scalar_one_or_none()
        if already is None:
            s.add(Track(path="/stats/one.flac", filename="one.flac", ext=".flac",
                        status=TrackStatus.ACTIVE, size_bytes=1000, duration=200.0,
                        lossless=True, has_artwork=True, album="Album",
                        albumartist="Artist"))
            s.add(Job(kind="scan", state=JobState.DONE))
            s.add(AuditLog(action="quarantine", level="info"))
            await s.commit()
    return app_client


# --------------------------------- statistics ---------------------------------

@pytest.mark.asyncio
async def test_statistics_answers_and_covers_the_window(client) -> None:
    body = client.get("/api/v1/statistics", params={"days": 30}).json()
    assert body["window_days"] == 30
    assert body["library"]["tracks"] >= 1
    assert "per_day" in body and body["per_day"], "the activity series is empty"
    # Quiet days are filled in, or a chart silently compresses a quiet week.
    assert len(body["per_day"]) >= 30


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, 400, 100000])
async def test_the_window_is_bounded(client, days: int) -> None:
    """An unbounded window is a full scan of the audit log on every load."""
    assert client.get("/api/v1/statistics",
                      params={"days": days}).status_code == 422


@pytest.mark.asyncio
async def test_statistics_requires_a_session(app_client) -> None:
    app_client.cookies.clear()
    for path in ("/api/v1/statistics", "/api/v1/statistics/log",
                 "/api/v1/statistics/summary"):
        assert app_client.get(path).status_code in (401, 403), f"{path} was not gated"


# ----------------------------------- the log -----------------------------------

@pytest.mark.asyncio
async def test_the_log_endpoint_takes_no_path(client) -> None:
    """The only file it can read is the one the application is writing.

    Anything that looks like a filename must be ignored rather than honoured --
    these are not expected to 400, they are expected to be *unused*, which is
    why the response still points at the real log.
    """
    real = str(Path(get_settings().config_dir) / "logs" / "cadenza.log")
    for attempt in ("/etc/passwd", "../../../etc/passwd", "auth.json",
                    "secret_key", "../auth.json"):
        for param in ("path", "file", "name", "log"):
            body = client.get("/api/v1/statistics/log",
                              params={param: attempt, "lines": 5}).json()
            assert body["path"] == real, \
                f"{param}={attempt!r} changed which file was read"


@pytest.mark.asyncio
async def test_the_log_is_bounded(client) -> None:
    assert client.get("/api/v1/statistics/log",
                      params={"lines": 100000}).status_code == 422
    assert client.get("/api/v1/statistics/log",
                      params={"lines": 0}).status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_level_is_refused(client) -> None:
    assert client.get("/api/v1/statistics/log",
                      params={"level": "TRACE"}).status_code == 422


@pytest.mark.asyncio
async def test_a_missing_log_is_reported_not_an_error(monkeypatch, tmp_path) -> None:
    """A fresh install has no log yet, which is not a failure.

    Called directly rather than over HTTP, and that is not laziness. Repointing
    `config_dir` also moves `auth.json`, so the request answers 401 before it
    reaches the branch under test — correct behaviour, and not what this is
    about. Moving the log file aside instead does not work either: the rotating
    handler holds it open, and Windows refuses to rename an open file.
    """
    from app.api.v1.statistics import application_log

    monkeypatch.setattr(get_settings(), "config_dir", tmp_path / "nothing-here")
    body = await application_log()
    assert body["exists"] is False, body
    assert body["lines"] == []
    assert "no log file yet" in body["note"]


def test_tail_reads_the_end_without_reading_the_whole_file(tmp_path) -> None:
    """The log rotates at 8 MB with five behind it.

    Reading it all to show the last screenful would be pointless on a NAS whose
    RAM is shared with everything else it runs.
    """
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"line {i}" for i in range(50_000)), encoding="utf-8")

    tail = _tail(path, 10)
    assert len(tail) == 10
    assert tail[-1] == "line 49999"
    assert tail[0] == "line 49990"


def test_tail_copes_with_a_file_shorter_than_the_request(tmp_path) -> None:
    path = tmp_path / "short.log"
    path.write_text("only one line\n", encoding="utf-8")
    assert _tail(path, 500) == ["only one line"]


def test_tail_copes_with_an_empty_file(tmp_path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")
    assert _tail(path, 10) == []
