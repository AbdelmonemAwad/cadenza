"""Failure throttling for the sign-in endpoints.

Without this, the password is protected only by how long scrypt takes. That is
a real cost to an attacker but not a limit: the endpoint answers as fast as the
thread pool allows, forever, and a NAS is commonly reachable from the whole
house or an exposed port.

Three things here were wrong in the first version and are worth stating,
because each one made the throttle look like protection while providing none.

**The key was attacker-chosen.** `client_key` read the left-most
X-Forwarded-For entry, which is only the real client if the proxy *overwrites*
the header. The bundled nginx used `$proxy_add_x_forwarded_for`, which
*appends*, so the left-most entry was whatever the caller wrote -- a fresh
bucket per request. Worse, uvicorn ran with `--forwarded-allow-ips='*'`, so it
had already rewritten `request.client` from that same forged header before the
app ever saw it. nginx now overwrites X-Forwarded-For with `$remote_addr`,
uvicorn trusts only the loopback, and this module prefers X-Real-IP, which
nginx has always set with the overwriting form.

**The check and the record were separate.** The endpoint called `check()`,
spent ~100 ms in scrypt, then called `record_failure()`. Two hundred concurrent
requests all passed the check before the first failure was recorded, so one
burst tested two hundred passwords against a limit of ten. An attempt is now
reserved and counted in the same lock acquisition that authorises it, before
any hashing happens; a success releases it.

**The global counter was a weapon.** It refused *every* key once tripped, with
no exemption for the owner and no way out, so sixty failed requests bought an
attacker an indefinite sign-in outage for the household -- renewable, because
throttled requests never reached `record_failure` and so never let the window
drain. It no longer refuses anything. It applies a delay instead, which slows a
distributed run to a crawl while leaving the legitimate owner able to sign in
after a short wait. A brake the attacker can stand on is worse than the attack
it was meant to prevent.

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

# Global: not a limit, a brake. Past this many recent failures across all
# callers, everyone waits a little before their password is checked. It never
# refuses, so it cannot be used to lock the owner out.
GLOBAL_SOFT_LIMIT = 60
GLOBAL_WINDOW_S = 15 * 60
# Grows with the overshoot, capped so a request is never parked indefinitely.
GLOBAL_DELAY_STEP_S = 0.25
GLOBAL_MAX_DELAY_S = 2.0

# Bounds the memory an attacker can make us allocate by rotating source
# addresses. Once full, the least recently seen entry is dropped.
MAX_TRACKED_CLIENTS = 4096

# Addresses trusted to have set the forwarding headers: nginx runs beside the
# app in the same container and proxies to it over the loopback.
_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1", "localhost"})

# A single host is routinely handed a whole IPv6 /64, so keying on the full
# address would give one machine an inexhaustible supply of fresh buckets.
_IPV6_BUCKET_PREFIX = 64


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)
    last_seen: float = 0.0


@dataclass(frozen=True)
class Decision:
    """The answer to "may this attempt proceed, and how soon?"."""

    retry_after: float = 0.0   # > 0 means refuse with 429
    delay: float = 0.0         # > 0 means proceed, but not immediately

    @property
    def blocked(self) -> bool:
        return self.retry_after > 0


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, _Bucket] = {}
        self._global: list[float] = []

    def reserve(self, key: str, *, now: float | None = None) -> Decision:
        """Check and count an attempt atomically.

        The attempt is recorded here rather than after the password is checked.
        Splitting those two steps left a window the width of scrypt -- ~100 ms
        -- in which every concurrent request saw counters no in-flight attempt
        had touched yet, so a burst of N requests consumed a single slot
        between them. Callers release the reservation with `record_success`.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)

            bucket = self._clients.get(key)
            if bucket and len(bucket.hits) >= PER_CLIENT_MAX:
                return Decision(
                    retry_after=max(0.0, PER_CLIENT_WINDOW_S - (now - bucket.hits[0])))

            if bucket is None:
                if len(self._clients) >= MAX_TRACKED_CLIENTS:
                    oldest = min(self._clients, key=lambda k: self._clients[k].last_seen)
                    del self._clients[oldest]
                bucket = self._clients[key] = _Bucket()
            bucket.hits.append(now)
            bucket.last_seen = now
            self._global.append(now)

            over = len(self._global) - GLOBAL_SOFT_LIMIT
            delay = min(GLOBAL_MAX_DELAY_S, over * GLOBAL_DELAY_STEP_S) if over > 0 else 0.0
            return Decision(delay=delay)

    def record_success(self, key: str) -> None:
        """Release this caller's reservations.

        Clears the per-address history so a few typos never lock anyone out
        once they get it right. The global counter is deliberately left alone:
        it measures total recent pressure, and one success elsewhere should not
        erase the evidence of a run in progress. Since it only ever delays,
        leaving it costs the owner at most a couple of seconds.
        """
        with self._lock:
            self._clients.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._clients.clear()
            self._global.clear()

    def tracked(self) -> int:
        with self._lock:
            return len(self._clients)

    def _expire(self, now: float) -> None:
        cutoff = now - GLOBAL_WINDOW_S
        # The global list is appended in time order, so expired entries are a
        # prefix: drop them by index instead of rebuilding the whole list.
        drop = 0
        for stamp in self._global:
            if stamp > cutoff:
                break
            drop += 1
        if drop:
            del self._global[:drop]

        # Only buckets old enough to have expired are examined. Sweeping every
        # tracked client on every request let an unauthenticated caller impose
        # an O(tracked) walk under a mutex, once per attempt.
        client_cutoff = now - PER_CLIENT_WINDOW_S
        for key, bucket in list(self._clients.items()):
            if bucket.last_seen > client_cutoff:
                continue
            bucket.hits = [t for t in bucket.hits if t > client_cutoff]
            if not bucket.hits:
                del self._clients[key]


login_limiter = RateLimiter()


def client_key(request) -> str:
    """A stable identifier for the caller that the caller cannot choose.

    X-Real-IP is preferred: nginx sets it with the overwriting form of
    proxy_set_header, so a value supplied by the client is replaced rather than
    prepended to. X-Forwarded-For is accepted as a fallback because nginx now
    also sets that from `$remote_addr` -- but either is read only when the
    connection itself came from the loopback, which is the only way the bundled
    nginx reaches the app.

    IPv6 addresses are keyed by their /64, since one machine is commonly handed
    the whole prefix and would otherwise never exhaust its buckets.
    """
    peer = request.client.host if request.client else "unknown"
    if peer not in _TRUSTED_PROXIES:
        return _bucket_for(peer)

    for header in ("x-real-ip", "x-forwarded-for"):
        candidate = request.headers.get(header, "").split(",")[0].strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue        # malformed: never key on attacker-chosen text
        return _bucket_for(candidate)
    return _bucket_for(peer)


def _bucket_for(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if parsed.version == 6:
        return str(ipaddress.ip_network(
            f"{parsed}/{_IPV6_BUCKET_PREFIX}", strict=False))
    return str(parsed)
