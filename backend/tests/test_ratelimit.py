"""Sign-in throttling and the CORS policy (the remainder of issue #5).

Before these, the password was protected only by how long scrypt takes, and any
page served from any port on localhost could make credentialed requests to the
API and read the replies.
"""
from __future__ import annotations

import pytest

from app.core.ratelimit import (
    GLOBAL_MAX_DELAY_S,
    GLOBAL_SOFT_LIMIT,
    MAX_TRACKED_CLIENTS,
    PER_CLIENT_MAX,
    PER_CLIENT_WINDOW_S,
    RateLimiter,
    client_key,
)


@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter()


# ------------------------------- Counting rules -------------------------------

def test_a_fresh_caller_is_allowed(limiter):
    assert not limiter.reserve("10.0.0.5").blocked


def test_the_caller_is_blocked_after_the_limit(limiter):
    for _ in range(PER_CLIENT_MAX):
        assert not limiter.reserve("10.0.0.5").blocked
    assert limiter.reserve("10.0.0.5").blocked


def test_reserving_counts_the_attempt_before_the_password_is_checked(limiter):
    """The concurrency defect, stated directly.

    check() and record_failure() used to be separate calls with ~100 ms of
    scrypt between them, so N concurrent requests all passed the check before
    any of them had been counted and one burst tested N passwords against a
    limit of PER_CLIENT_MAX. Reserving without ever reporting an outcome must
    still exhaust the budget.
    """
    allowed = sum(0 if limiter.reserve("10.0.0.5").blocked else 1 for _ in range(200))
    assert allowed == PER_CLIENT_MAX


def test_concurrent_reservations_do_not_exceed_the_limit(limiter):
    """Same thing from real threads, since the endpoint hashes in a worker."""
    import threading

    allowed, lock = [], threading.Lock()
    start = threading.Barrier(20)

    def attempt() -> None:
        start.wait()
        if not limiter.reserve("10.0.0.5").blocked:
            with lock:
                allowed.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(allowed) == PER_CLIENT_MAX


def test_blocking_one_caller_does_not_block_another(limiter):
    for _ in range(PER_CLIENT_MAX):
        limiter.reserve("10.0.0.5")
    assert limiter.reserve("10.0.0.5").blocked
    assert not limiter.reserve("10.0.0.6").blocked


def test_a_success_clears_the_history(limiter):
    """Otherwise a few typos would lock someone out for the rest of the window
    even after they got it right."""
    for _ in range(PER_CLIENT_MAX - 1):
        limiter.reserve("10.0.0.5")
    limiter.record_success("10.0.0.5")
    for _ in range(PER_CLIENT_MAX):
        assert not limiter.reserve("10.0.0.5").blocked


def test_the_window_expires(limiter):
    now = 1000.0
    for _ in range(PER_CLIENT_MAX):
        limiter.reserve("10.0.0.5", now=now)
    assert limiter.reserve("10.0.0.5", now=now).blocked
    assert not limiter.reserve("10.0.0.5", now=now + PER_CLIENT_WINDOW_S + 1).blocked


def test_retry_after_shrinks_as_the_window_passes(limiter):
    now = 1000.0
    for _ in range(PER_CLIENT_MAX):
        limiter.reserve("10.0.0.5", now=now)
    early = limiter.reserve("10.0.0.5", now=now + 10).retry_after
    later = limiter.reserve("10.0.0.5", now=now + 200).retry_after
    assert 0 < later < early


# ------------------------------ The global counter ------------------------------

def test_the_global_counter_never_refuses_anyone(limiter):
    """The owner-lockout defect, stated directly.

    The global counter used to refuse every key once tripped, with no exemption
    for the owner and no way to drain it -- throttled requests never reached
    record_failure, so an attacker polling at 1 req/s pinned it forever. Sixty
    failed requests bought an indefinite sign-in outage for the household. It
    now slows callers down instead, so an outage is not for sale.
    """
    for i in range(GLOBAL_SOFT_LIMIT * 3):
        limiter.reserve(f"10.0.{i // 256}.{i % 256}")

    owner = limiter.reserve("192.168.1.10")     # never failed once
    assert not owner.blocked, "an attacker locked the owner out"


def test_global_pressure_slows_callers_down(limiter):
    """It still has to cost something, or it is not a control at all."""
    for i in range(GLOBAL_SOFT_LIMIT):
        assert limiter.reserve(f"10.0.{i // 256}.{i % 256}").delay == 0.0

    delays = [limiter.reserve(f"172.16.{i // 256}.{i % 256}").delay for i in range(20)]
    assert delays[0] > 0
    assert delays[-1] > delays[0], "the brake should tighten as pressure grows"


def test_the_delay_is_capped(limiter):
    """An uncapped delay is just a slower outage, and parks server workers."""
    for i in range(GLOBAL_SOFT_LIMIT * 10):
        limiter.reserve(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}")
    assert limiter.reserve("192.168.1.10").delay <= GLOBAL_MAX_DELAY_S


def test_rotating_the_source_address_is_still_noticed(limiter):
    """The per-address counter is only as good as the address, so the global
    brake has to engage when someone rotates through fresh ones."""
    for i in range(GLOBAL_SOFT_LIMIT + 20):
        limiter.reserve(f"10.0.{i // 256}.{i % 256}")
    assert limiter.reserve("10.99.99.99").delay > 0


def test_tracking_is_bounded(limiter):
    """An attacker rotating addresses must not be able to grow the table
    without limit."""
    for i in range(MAX_TRACKED_CLIENTS + 500):
        limiter.reserve(f"key-{i}")
    assert limiter.tracked() <= MAX_TRACKED_CLIENTS


# --------------------------------- Client key ---------------------------------

class _Req:
    def __init__(self, peer: str | None, headers: dict[str, str] | None = None) -> None:
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = headers or {}


def test_a_direct_caller_is_keyed_on_its_own_address():
    assert client_key(_Req("192.168.1.40")) == "192.168.1.40"


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored():
    """Otherwise every attacker picks their own bucket by sending a header,
    which is worse than no per-address limit because it looks like one."""
    request = _Req("192.168.1.40", {"x-forwarded-for": "10.0.0.1", "x-real-ip": "10.0.0.2"})
    assert client_key(request) == "192.168.1.40"


def test_x_real_ip_is_preferred_over_x_forwarded_for():
    """The bypass, and why the preference exists.

    nginx sets X-Real-IP with the overwriting form of proxy_set_header, so a
    client-supplied value is replaced. X-Forwarded-For was set with
    $proxy_add_x_forwarded_for, which APPENDS -- leaving the left-most entry,
    the one everything treats as the origin, written by the caller. Reading it
    handed the attacker a fresh bucket per request. nginx now overwrites both,
    but the preference stays: if the two ever disagree, the unspoofable one
    wins.
    """
    request = _Req("127.0.0.1", {
        "x-forwarded-for": "203.0.113.9, 192.168.1.40",   # forged, then real
        "x-real-ip": "192.168.1.40",
    })
    assert client_key(request) == "192.168.1.40"


def test_a_forged_header_cannot_buy_a_fresh_bucket(limiter):
    """End to end: rotating the header must not reset the throttle."""
    for i in range(PER_CLIENT_MAX):
        key = client_key(_Req("127.0.0.1", {
            "x-real-ip": "192.168.1.40",
            "x-forwarded-for": f"203.0.113.{i}, 192.168.1.40"}))
        assert not limiter.reserve(key).blocked

    key = client_key(_Req("127.0.0.1", {
        "x-real-ip": "192.168.1.40",
        "x-forwarded-for": "203.0.113.250, 192.168.1.40"}))
    assert limiter.reserve(key).blocked, "a forged header bought a new bucket"


def test_a_forwarded_header_from_the_local_proxy_is_used():
    """nginx runs beside the app in the same container and reaches it over the
    loopback, so this is the real client."""
    request = _Req("127.0.0.1", {"x-forwarded-for": "192.168.1.40"})
    assert client_key(request) == "192.168.1.40"


def test_a_malformed_forwarded_header_falls_back_to_the_peer():
    request = _Req("127.0.0.1", {"x-forwarded-for": "not-an-address"})
    assert client_key(request) == "127.0.0.1"


def test_a_malformed_real_ip_falls_through_to_forwarded_for():
    request = _Req("127.0.0.1", {"x-real-ip": "nonsense",
                                 "x-forwarded-for": "192.168.1.40"})
    assert client_key(request) == "192.168.1.40"


def test_a_missing_client_does_not_raise():
    assert client_key(_Req(None)) == "unknown"


def test_ipv6_callers_are_keyed_by_prefix_not_by_address():
    """A single machine is routinely handed a whole /64. Keying on the full
    address would give it an inexhaustible supply of fresh buckets."""
    keys = {
        client_key(_Req(f"2001:db8:0:1::{i}"))
        for i in range(1, 6)
    }
    assert len(keys) == 1, f"one host produced {len(keys)} buckets: {keys}"


def test_different_ipv6_prefixes_stay_separate():
    a = client_key(_Req("2001:db8:0:1::1"))
    b = client_key(_Req("2001:db8:0:2::1"))
    assert a != b


# ----------------------------- Over the real API -----------------------------

def test_login_answers_429_once_the_limit_is_reached(app_client):
    from app.core.auth import Credentials, hash_password, save_credentials
    from app.core.ratelimit import login_limiter

    save_credentials(Credentials(password_hash=hash_password("rate-limit-test-password")))
    login_limiter.reset()
    app_client.cookies.clear()
    try:
        for _ in range(PER_CLIENT_MAX):
            assert app_client.post("/api/v1/auth/login",
                                   json={"password": "wrong"}).status_code == 401

        blocked = app_client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0

        # The correct password is refused too, for this address. Otherwise the
        # throttle would only slow down an attacker who already had it.
        assert app_client.post(
            "/api/v1/auth/login",
            json={"password": "rate-limit-test-password"}).status_code == 429
    finally:
        login_limiter.reset()


# ------------------------------------ CORS ------------------------------------

def test_cross_origin_requests_are_not_allowed_by_default(app_client):
    """A deployed instance serves the UI from the same origin through nginx, so
    no cross-origin request should ever carry the session cookie. This used to
    allow any http://localhost:<port> with credentials, which is every other
    web app on the machine."""
    response = app_client.get(
        "/health", headers={"Origin": "http://localhost:9999"})
    assert "access-control-allow-origin" not in {
        k.lower() for k in response.headers}


def test_the_dev_origin_setting_is_empty_by_default():
    from app.config import get_settings

    assert get_settings().cors_dev_origins == ()


def test_the_dev_origin_setting_cannot_be_changed_over_the_api():
    from app.core.settings_policy import SettingRejected, validate_patch

    with pytest.raises(SettingRejected):
        validate_patch({"cors_dev_origins": ["http://evil.example"]})
