"""Every read endpoint answers, and the main journeys complete.

The unit tests cover engines in isolation and `test_api_smoke` checks a handful
of routes. Neither notices a page that reads a field the API stopped returning,
a filter that silently ignores its argument, or a whole feature that 500s the
moment there is real data in the database — which is the state every user is in
and no other test simulates.

So this seeds a small library, then walks the application the way the interface
does: list, filter, drill in, run a job, quarantine something, restore it.
"""
from __future__ import annotations

import pytest

from app.core.auth import Credentials, hash_password, save_credentials
from app.db.base import SessionFactory, init_db
from app.db.models import (
    AuditLog,
    DupAction,
    DuplicateGroup,
    DuplicateMember,
    Job,
    JobState,
    QuarantineItem,
    Track,
    TrackStatus,
)

_PASSWORD = "end-to-end-test-password"

# Every GET that must answer on a populated database. A 500 here is a feature
# that is broken for anyone who has actually used the app.
READ_ENDPOINTS = [
    "/api/v1/settings",
    "/api/v1/settings/health",
    "/api/v1/settings/writable",
    "/api/v1/dashboard/stats",
    "/api/v1/dashboard/recent-jobs",
    "/api/v1/dashboard/audit",
    "/api/v1/library/tracks",
    "/api/v1/library/albums",
    "/api/v1/library/codecs",
    "/api/v1/duplicates/groups",
    "/api/v1/duplicates/report",
    "/api/v1/quarantine",
    "/api/v1/quarantine/stats",
    "/api/v1/convert/presets",
    "/api/v1/convert/candidates",
    "/api/v1/convert/codecs",
    "/api/v1/jobs",
    "/api/v1/jobs/kinds",
    "/api/v1/jobs/schedule/tasks",
    "/api/v1/apple/status",
    "/api/v1/files/roots",
    "/api/v1/files/credentials",
]


def _track(path: str, **kw) -> Track:
    from pathlib import Path
    base = {
        "path": path,
        "filename": Path(path).name,
        "ext": Path(path).suffix,
        "size_bytes": 5_000_000,
        "status": TrackStatus.ACTIVE,
        "codec": "flac",
        "lossless": True,
        "bitrate": 900_000,
        "sample_rate": 44100,
        "duration": 210.0,
        "title": Path(path).stem,
        "artist": "An Artist",
        "albumartist": "An Artist",
        "album": "An Album",
        "year": 2001,
        "track_no": 1,
        "has_artwork": True,
        "has_lyrics": False,
        "tag_completeness": 0.8,
        "quality_score": 0.8,
        "sha256": "a" * 64,
    }
    base.update(kw)
    return Track(**base)


# The seed is built once and reused. The fixture cannot be module-scoped --
# pytest-asyncio gives fixtures a function-scoped loop here, and a module-scoped
# async fixture asking for it raises ScopeMismatch -- so the caching is explicit
# instead. Re-seeding would fail anyway: Track.path is unique.
_SEEDED: dict[str, int] = {}


@pytest.fixture
async def populated(app_client):
    """A signed-in client over a database that looks like it has been used."""
    await init_db()
    save_credentials(Credentials(username="e2e", password_hash=hash_password(_PASSWORD)))
    app_client.cookies.clear()
    r = app_client.post("/api/v1/auth/login",
                        json={"username": "e2e", "password": _PASSWORD})
    assert r.status_code == 200, r.text

    if _SEEDED:
        return app_client, _SEEDED

    async with SessionFactory() as s:
        a = _track("/music/An Artist/An Album/01 - One.flac")
        b = _track("/music/An Artist/An Album/02 - Two.mp3", codec="mp3",
                   lossless=False, bitrate=192_000, ext=".mp3", sha256="b" * 64)
        c = _track("/music/An Artist/An Album/03 - Old.wma", codec="wmav2",
                   lossless=False, bitrate=128_000, ext=".wma", sha256="c" * 64)
        s.add_all([a, b, c])
        await s.flush()

        group = DuplicateGroup(kind="exact_audio", signature="sig-1", confidence=1.0,
                               member_count=2, reclaimable_bytes=5_000_000)
        s.add(group)
        await s.flush()
        s.add_all([
            DuplicateMember(group_id=group.id, track_id=a.id, score=0.9,
                            proposed_action=DupAction.KEEP),
            DuplicateMember(group_id=group.id, track_id=b.id, score=0.4,
                            proposed_action=DupAction.QUARANTINE),
        ])
        s.add(Job(kind="scan", state=JobState.DONE, total=3, processed=3))
        s.add(AuditLog(action="quarantine", level="info",
                       src_path=a.path, dst_path="/quarantine/x.flac"))
        s.add(QuarantineItem(original_path=c.path, quarantine_path="/quarantine/03.wma",
                             size_bytes=1000, reason="test"))
        await s.commit()
        _SEEDED.update({"a": a.id, "b": b.id, "c": c.id, "group": group.id})

    return app_client, _SEEDED


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_every_read_endpoint_answers_on_a_used_database(populated, path) -> None:
    client, _ = populated
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:300]}"


async def test_library_filters_actually_filter(populated) -> None:
    """A filter that is accepted and ignored is worse than one that 400s."""
    client, _ = populated

    everything = client.get("/api/v1/library/tracks", params={"limit": 100}).json()
    assert everything["total"] >= 3

    lossless = client.get("/api/v1/library/tracks",
                          params={"lossless": "true", "limit": 100}).json()
    assert lossless["total"] >= 1
    assert all(item["lossless"] for item in lossless["items"]), \
        "the lossless filter returned lossy tracks"

    by_codec = client.get("/api/v1/library/tracks",
                          params={"codec": "mp3", "limit": 100}).json()
    assert by_codec["total"] >= 1
    assert {i["codec"] for i in by_codec["items"]} == {"mp3"}, \
        "the codec filter returned other codecs"

    searched = client.get("/api/v1/library/tracks",
                          params={"q": "Old", "limit": 100}).json()
    assert searched["total"] >= 1, "search found nothing for a title that exists"


async def test_a_single_track_and_its_album_can_be_opened(populated) -> None:
    client, ids = populated

    one = client.get(f"/api/v1/library/tracks/{ids['a']}")
    assert one.status_code == 200
    assert one.json()["id"] == ids["a"]

    assert client.get("/api/v1/library/tracks/99999").status_code == 404

    albums = client.get("/api/v1/library/albums").json()
    assert albums, "the album view is empty although tracks have an album"


async def test_duplicate_groups_carry_their_members(populated) -> None:
    """The page renders members; a group without them is a blank row."""
    client, ids = populated
    groups = client.get("/api/v1/duplicates/groups").json()
    items = groups["items"] if isinstance(groups, dict) else groups
    assert items, "no duplicate groups were returned"
    target = next((g for g in items if g["id"] == ids["group"]), None)
    assert target is not None
    assert target.get("members"), "the group came back with no members"


async def test_the_conversion_engine_suggests_the_legacy_file(populated) -> None:
    """wmav2 is in LEGACY_CODECS, so it must be offered as a candidate."""
    client, _ = populated
    candidates = client.get("/api/v1/convert/candidates").json()
    paths = {c["path"] for c in candidates["items"]}
    assert any(p.endswith(".wma") for p in paths), \
        "a legacy WMA file was not offered for conversion"


async def test_a_job_can_be_submitted_listed_and_cancelled(populated) -> None:
    client, _ = populated

    kinds = client.get("/api/v1/jobs/kinds").json()
    assert "scan" in kinds and "fingerprint" in kinds

    submitted = client.post("/api/v1/jobs", json={"kind": "scan", "params": {},
                                                  "dry_run": True})
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["job_id"]

    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 200
    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200

    assert client.post("/api/v1/jobs", json={"kind": "no-such-kind"}).status_code == 400


async def test_settings_round_trip_and_refuse_what_they_should(populated) -> None:
    client, _ = populated

    before = client.get("/api/v1/settings").json()
    assert before["dry_run_default"] in (True, False)

    ok = client.patch("/api/v1/settings", json={"quarantine_retention_days": 45})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/v1/settings").json()["quarantine_retention_days"] == 45

    # Deployment configuration, locked on purpose (issue #6).
    assert client.patch("/api/v1/settings",
                        json={"music_root": "/etc"}).status_code == 400
    assert client.patch("/api/v1/settings",
                        json={"ffmpeg_bin": "/bin/sh"}).status_code == 400
    # Out of range rather than unknown.
    assert client.patch("/api/v1/settings",
                        json={"quarantine_retention_days": 0}).status_code == 400


async def test_a_secret_is_never_echoed_back(populated) -> None:
    client, _ = populated
    client.patch("/api/v1/settings", json={"lastfm_api_key": "super-secret-value"})
    body = client.get("/api/v1/settings").text
    assert "super-secret-value" not in body, "an API key was returned to the browser"
    assert client.get("/api/v1/settings").json()["lastfm_api_key"] is True


async def test_quarantine_lists_and_restores(populated) -> None:
    client, _ = populated
    listing = client.get("/api/v1/quarantine").json()
    items = listing["items"] if isinstance(listing, dict) else listing
    assert items, "the quarantine is empty although an item was added"

    stats = client.get("/api/v1/quarantine/stats").json()
    assert isinstance(stats, dict)

    # The file is not on disk in this fixture, so restoring must fail cleanly
    # rather than 500.
    response = client.post(f"/api/v1/quarantine/{items[0]['id']}/restore")
    assert response.status_code in (200, 400, 404, 409), response.text


async def test_purging_is_refused_while_hard_delete_is_off(populated) -> None:
    """The safety invariant the whole project rests on.

    A dry run is a preview and writes nothing, so it is accepted whatever the
    setting says. Only the destructive form is refused -- and it is refused
    here, at the endpoint, as well as inside the job: asking for a purge with
    permanent deletion off used to return a job id and a 200, and the refusal
    surfaced only in the job's result, so the interface said "started" and
    nothing happened.
    """
    client, _ = populated
    client.patch("/api/v1/settings", json={"hard_delete_allowed": False})

    preview = client.post("/api/v1/quarantine/purge", json={"dry_run": True})
    assert preview.status_code == 200, "a preview must always be allowed"

    real = client.post("/api/v1/quarantine/purge", json={"dry_run": False})
    assert real.status_code == 409, \
        f"a purge was accepted with permanent deletion off: {real.status_code}"
    assert "Settings" in real.json()["detail"], \
        "the refusal does not say how to change it"


async def test_signing_out_really_ends_the_session(populated) -> None:
    client, _ = populated
    assert client.get("/api/v1/library/tracks").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code in (200, 204)
    assert client.get("/api/v1/library/tracks").status_code in (401, 403)

    client.post("/api/v1/auth/login", json={"username": "e2e", "password": _PASSWORD})
    assert client.get("/api/v1/library/tracks").status_code == 200
