"""Numbers the application showed that were not true.

None of these failed. They reported a figure, and the figure was wrong — which
is worse, because there is nothing to notice.
"""
from __future__ import annotations

import pytest

from app.core.dedup import metadata_keys
from app.db.models import Track, TrackStatus

_PASSWORD = "counts-test-password"


@pytest.fixture
async def populated_library(app_client):
    """A signed-in client over a library holding every track status."""
    from app.core.auth import Credentials, hash_password, save_credentials
    from app.db.base import SessionFactory, init_db
    from sqlalchemy import select

    await init_db()
    save_credentials(Credentials(username="counts",
                                 password_hash=hash_password(_PASSWORD)))
    app_client.cookies.clear()
    r = app_client.post("/api/v1/auth/login",
                        json={"username": "counts", "password": _PASSWORD})
    assert r.status_code == 200, r.text

    async with SessionFactory() as s:
        for status in (TrackStatus.ACTIVE, TrackStatus.QUARANTINED,
                       TrackStatus.MISSING, TrackStatus.CORRUPT):
            path = f"/counts/{status.value}.flac"
            found = (await s.execute(
                select(Track).where(Track.path == path))).scalar_one_or_none()
            if found is None:
                s.add(_t(path=path, filename=f"{status.value}.flac",
                         status=status, title=f"Counts {status.value}",
                         duration=200.0))
        await s.commit()
    return app_client


def _t(**kw) -> Track:
    base = {"path": "/m/x.flac", "filename": "x.flac", "ext": ".flac",
            "size_bytes": 1, "status": TrackStatus.ACTIVE}
    base.update(kw)
    return Track(**base)


# ------------------------- duplicate blocking window -------------------------

def test_two_copies_a_second_apart_share_a_blocking_key() -> None:
    """The window has to be at least as wide as the tolerance it feeds.

    Only tracks sharing a key are ever compared. At a fixed five seconds,
    182.4s bucketed to 36 and 182.6s to 37, so the pair was never looked at --
    even though the engine would have accepted a seven-second difference. Most
    real duplicates differ by a second or two of encoder padding, so they fell
    either side of a boundary about as often as not.
    """
    a = _t(title="Same Song", artist="An Artist", duration=182.4)
    b = _t(title="Same Song", artist="An Artist", duration=182.6)
    assert set(metadata_keys(a, 7.0)) & set(metadata_keys(b, 7.0)), \
        "two copies 0.2s apart were never going to be compared"


@pytest.mark.parametrize("gap", [0.1, 1.0, 3.0, 6.0])
def test_any_pair_inside_the_tolerance_meets(gap: float) -> None:
    a = _t(title="Same Song", artist="An Artist", duration=200.0)
    b = _t(title="Same Song", artist="An Artist", duration=200.0 + gap)
    assert set(metadata_keys(a, 7.0)) & set(metadata_keys(b, 7.0)), \
        f"a {gap}s difference did not share a key, inside a 7s tolerance"


def test_tracks_far_apart_do_not_share_a_key() -> None:
    """The blocking still has to block, or every title becomes O(n^2)."""
    a = _t(title="Same Song", artist="An Artist", duration=100.0)
    b = _t(title="Same Song", artist="An Artist", duration=400.0)
    assert not set(metadata_keys(a, 7.0)) & set(metadata_keys(b, 7.0))


def test_a_track_with_no_duration_produces_no_keys() -> None:
    assert metadata_keys(_t(title="Song", artist="A"), 7.0) == []
    assert metadata_keys(_t(title=None, duration=180.0), 7.0) == []


# ------------------------------- library list -------------------------------

@pytest.mark.asyncio
async def test_the_track_list_shows_only_active_tracks(populated_library) -> None:
    """Quarantined, missing and corrupt rows were counted as library tracks.

    So the dashboard and the library page disagreed, and a file the user had
    already moved to quarantine kept appearing in a list of their music.
    """
    client = populated_library
    body = client.get("/api/v1/library/tracks", params={"limit": 200}).json()
    statuses = {item["status"] for item in body["items"]}
    assert statuses <= {"active", TrackStatus.ACTIVE.value}, \
        f"the list included {statuses - {'active'}}"


@pytest.mark.asyncio
async def test_asking_for_a_status_still_returns_it(populated_library) -> None:
    """Defaulting to active must not make the other statuses unreachable."""
    client = populated_library
    body = client.get("/api/v1/library/tracks",
                      params={"status": "quarantined", "limit": 200}).json()
    assert body["total"] >= 1, "quarantined tracks cannot be listed at all"


@pytest.mark.asyncio
async def test_search_is_not_capped_at_five_thousand(populated_library) -> None:
    """The old form fetched up to 5000 rowids and passed them to IN(...).

    A library where more than 5000 tracks matched lost everything past the cap
    and reported the capped number as the total, with no indication that
    anything had been dropped. The subquery has no limit.
    """
    import inspect

    from app.api.v1 import library
    source = inspect.getsource(library.list_tracks)
    assert "LIMIT 5000" not in source, \
        "search still truncates at 5000 and reports that as the total"
