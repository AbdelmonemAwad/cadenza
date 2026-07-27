"""Shared provider contract: rate limiting, retries and a persistent cache."""
from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings
from app.db.models import ProviderCache

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TrackMetadata:
    """Uniform result shape returned by every provider."""
    source: str = ""
    confidence: float = 0.0            # 0..1, the provider's faith in this match

    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    year: int | None = None
    date: str | None = None
    track_no: int | None = None
    total_tracks: int | None = None
    disc_no: int | None = None
    total_discs: int | None = None
    genre: str | None = None
    composer: str | None = None
    isrc: str | None = None
    compilation: bool = False

    mb_recording_id: str | None = None
    mb_release_id: str | None = None
    mb_artist_id: str | None = None
    acoustid: str | None = None
    apple_id: str | None = None
    discogs_release_id: str | None = None

    artwork_url: str | None = None
    artwork_px: int | None = None
    lyrics: str | None = None
    synced_lyrics: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)


class RateLimiter:
    """Sliding-window limiter. MusicBrainz enforces one request per second."""

    def __init__(self, calls: int, period: float) -> None:
        self.calls, self.period = calls, period
        self._hits: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._hits = [h for h in self._hits if now - h < self.period]
                if len(self._hits) < self.calls:
                    self._hits.append(now)
                    return
                await asyncio.sleep(self.period - (now - self._hits[0]) + 0.01)


class ProviderError(Exception):
    pass


class RetryableError(ProviderError):
    pass


class BaseProvider(abc.ABC):
    name: str = "base"
    rate_calls: int = 5
    rate_period: float = 1.0
    cache_ttl_days: int = 30

    def __init__(self, session: AsyncSession | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.settings = get_settings()
        self.session = session
        self._client = client
        self._limiter = RateLimiter(self.rate_calls, self.rate_period)

    # ---- HTTP ----

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={"User-Agent": self.settings.ua_header()},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        await self._limiter.acquire()
        resp = await self.client.request(method, url, **kw)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "5"))
            log.warning("%s rate limited, sleeping %.1fs", self.name, retry_after)
            await asyncio.sleep(min(retry_after, 60))
            raise RetryableError("429")
        if 500 <= resp.status_code < 600:
            raise RetryableError(str(resp.status_code))
        if resp.status_code == 404:
            return resp                     # "not found" is a valid answer, not an error
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    async def get_json(self, url: str, **kw) -> Any:
        r = await self._request("GET", url, **kw)
        if r.status_code == 404:
            return None
        try:
            return r.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.name}: invalid JSON response") from exc

    # ---- Cache ----

    @staticmethod
    def cache_key(*parts: Any) -> str:
        return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()

    async def cache_get(self, key: str) -> Any | None:
        if self.session is None:
            return None
        row = (await self.session.execute(
            select(ProviderCache).where(
                ProviderCache.provider == self.name,
                ProviderCache.cache_key == key,
            )
        )).scalar_one_or_none()
        if row is None:
            return None
        if row.expires_at and row.expires_at < datetime.now(UTC).replace(tzinfo=None):
            return None
        return row.payload

    async def cache_put(self, key: str, payload: Any) -> None:
        if self.session is None:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + timedelta(days=self.cache_ttl_days)
        stmt = sqlite_insert(ProviderCache).values(
            provider=self.name, cache_key=key, payload=payload,
            fetched_at=now, expires_at=expires,
        ).on_conflict_do_update(
            index_elements=["provider", "cache_key"],
            set_={"payload": payload, "fetched_at": now, "expires_at": expires},
        )
        await self.session.execute(stmt)

    async def cached_json(self, key_parts: tuple, url: str, **kw) -> Any:
        key = self.cache_key(*key_parts)
        hit = await self.cache_get(key)
        if hit is not None:
            return hit
        data = await self.get_json(url, **kw)
        if data is not None:
            await self.cache_put(key, data)
        return data

    # ---- Contract ----

    @property
    def enabled(self) -> bool:
        return True

    @abc.abstractmethod
    async def lookup(self, *, title: str | None = None, artist: str | None = None,
                     album: str | None = None, duration: float | None = None,
                     mbid: str | None = None, isrc: str | None = None,
                     fingerprint: str | None = None) -> list[TrackMetadata]:
        """Return candidates ordered by descending confidence."""
        raise NotImplementedError
