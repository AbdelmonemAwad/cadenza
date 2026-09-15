"""The provider cache must survive six providers looking a track up at once.

A lookup fans out to every secondary provider with `asyncio.gather`, and each
provider read and wrote its cache rows through the caller's session -- one
AsyncSession, used from six coroutines at the same time. SQLAlchemy refuses
that: "This session is provisioning a new connection; concurrent operations
are not permitted". The provider that lost the race was dropped from the
lookup with a warning, on almost every track (298 times in one preview run),
and its result never reached the merge.

The same design held SQLite's single write lock for far too long: the first
cache miss of a track opened the caller's write transaction, and it stayed
open across every network call that followed, until the track was committed.
Everything else that writes -- the progress row, a click in the interface --
waited out `busy_timeout` and failed with "database is locked".
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.base import SessionFactory, init_db
from app.db.models import ProviderCache
from app.providers.base import BaseProvider, TrackMetadata

pytestmark = pytest.mark.asyncio


class _Fake(BaseProvider):
    """A provider whose network call is a coroutine boundary and nothing else."""

    def __init__(self, session, name: str) -> None:
        super().__init__(session, client=None)
        self.name = name
        self.fetched = 0

    async def get_json(self, url: str, **_kw):  # type: ignore[override]
        self.fetched += 1
        await asyncio.sleep(0)
        return {"url": url, "by": self.name}

    async def lookup(self, **_kw) -> list[TrackMetadata]:
        return []


async def test_six_providers_share_a_session_and_none_is_dropped() -> None:
    await init_db()
    async with SessionFactory() as s:
        providers = [_Fake(s, name=f"p{i}") for i in range(6)]
        results = await asyncio.gather(
            *(p.cached_json(("k",), f"https://x/{p.name}") for p in providers),
            return_exceptions=True)

    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, f"a provider lost the race for the shared session: {failures[0]!r}"
    assert [r["by"] for r in results] == [p.name for p in providers]

    async with SessionFactory() as s:
        rows = (await s.execute(select(ProviderCache))).scalars().all()
    assert {r.provider for r in rows} == {p.name for p in providers}


async def test_a_cache_write_is_committed_on_its_own_not_with_the_caller() -> None:
    """The row must be visible from another session before the caller
    commits: a cache write that rides the caller's transaction holds the
    write lock for as long as the caller takes, which is a whole track's
    worth of network calls."""
    await init_db()
    async with SessionFactory() as caller:
        p = _Fake(caller, name="solo")
        await p.cached_json(("k",), "https://x/solo")
        assert p.fetched == 1

        async with SessionFactory() as other:
            row = (await other.execute(
                select(ProviderCache).where(ProviderCache.provider == "solo")
            )).scalar_one_or_none()
        assert row is not None, "the cache row waited for the caller's commit"
        assert row.payload == {"url": "https://x/solo", "by": "solo"}

        # The second call is a hit, served without a fetch.
        assert await p.cached_json(("k",), "https://x/solo") == row.payload
        assert p.fetched == 1


async def test_no_session_means_no_cache() -> None:
    p = _Fake(None, name="off")
    assert await p.cached_json(("k",), "https://x/off") == {"url": "https://x/off", "by": "off"}
    assert await p.cached_json(("k",), "https://x/off") == {"url": "https://x/off", "by": "off"}
    assert p.fetched == 2
