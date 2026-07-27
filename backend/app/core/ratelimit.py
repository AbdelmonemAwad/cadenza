"""Failure throttling for the sign-in endpoints.

Without this, the password is protected only by how long scrypt takes. That is
a real cost to an attacker but not a limit: the endpoint answers as fast as the
thread pool allows, forever, and a NAS is commonly reachable from the whole
house or an exposed port. A dictionary run against a human-chosen password gets
there.

Two counters, because each covers the other's blind spot:

  * Per client address, so one attacker is stopped quickly.
  * Global, because the per-address counter is only as good as the address. An
    attacker with a range of source addresses, or one able to influence
    X-Forwarded-For, gets a fresh bucket each time; the global counter does not
    care where the attempts came from.

The global limit is deliberately far above anything a household generates, so
in practice the per-address limit is what a mistyped password meets. Only
failures are counted and a success clears the caller's bucket, so normal use
never accumulates anything.

State is in memory on purpose. It is lost on restart, which is acceptable: an
attacker who can restart the container has already won, and persisting it would
turn a lockout into a file an attacker could aim at.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field

# Per address: a person who cannot remember their password gets several tries.
PER_CLIENT_MAX = 10
PER_CLIENT_WINDOW_S = 15 * 60

# Global: well above household use, well below what a dictionary run needs.
GLOBAL_MAX = 60
GLOBAL_WINDOW_S = 15 * 60

# Bounds the memory an attacker can make us allocate by rotating source
# addresses. Once full, the least recently seen entry is dropped.
MAX_TRACKED_CLIENTS = 4096

# Addresses trusted to have set X-Forwarded-For: nginx runs beside the app in
# the same container and proxies to it over the loopback. A header arriving
# from anywhere else is attacker-controlled and ignored.
_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)
    last_seen: float = 0.0


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, _Bucket] = {}
        self._global: list[float] = []

    def check(self, key: str, *, now: float | None = None) -> float:
        """Seconds the caller must wait, or 0.0 if the attempt may proceed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            if len(self._global) >= GLOBAL_MAX:
                return max(0.0, GLOBAL_WINDOW_S - (now - self._global[0]))
            bucket = self._clients.get(key)
            if bucket and len(bucket.hits) >= PER_CLIENT_MAX:
                return max(0.0, PER_CLIENT_WINDOW_S - (now - bucket.hits[0]))
            return 0.0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            bucket = self._clients.get(key)
            if bucket is None:
                if len(self._clients) >= MAX_TRACKED_CLIENTS:
                    oldest = min(self._clients, key=lambda k: self._clients[k].last_seen)
                    del self._clients[oldest]
                bucket = self._clients[key] = _Bucket()
            bucket.hits.append(now)
            bucket.last_seen = now
            self._global.append(now)

    def record_success(self, key: str) -> None:
        """Clear this caller's history. The global counter is left alone: it
        exists to bound total failures, and one success elsewhere should not
        wipe the evidence of an ongoing run."""
        with self._lock:
            self._clients.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._clients.clear()
            self._global.clear()

    def _expire(self, now: float) -> None:
        cutoff = now - GLOBAL_WINDOW_S
        self._global = [t for t in self._global if t > cutoff]
        client_cutoff = now - PER_CLIENT_WINDOW_S
        for key, bucket in list(self._clients.items()):
            bucket.hits = [t for t in bucket.hits if t > client_cutoff]
            if not bucket.hits:
                del self._clients[key]


login_limiter = RateLimiter()


def client_key(request) -> str:
    """A stable identifier for the caller.

    X-Forwarded-For is honoured only when the connection itself came from the
    loopback, which is the only way the bundled nginx reaches the app. Trusting
    it unconditionally would let any caller pick their own bucket by sending a
    header, which is worse than having no per-address limit at all because it
    looks like one.
    """
    peer = request.client.host if request.client else "unknown"
    if peer not in _TRUSTED_PROXIES:
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    # Left-most entry is the original client; the rest were added by proxies.
    candidate = forwarded.split(",")[0].strip()
    if not candidate:
        return peer
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        # Malformed header: fall back rather than key on attacker-chosen text.
        return peer
