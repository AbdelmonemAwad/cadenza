"""Sign-in throttling and the CORS policy (the remainder of issue #5).

Before these, the password was protected only by how long scrypt takes, and any
page served from any port on localhost could make credentialed requests to the
API and read the replies.
"""
from __future__ import annotations

import pytest

from app.core.ratelimit import (
    GLOBAL_MAX,
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
    assert limiter.check("10.0.0.5") == 0.0


def test_the_caller_is_blocked_after_the_limit(limiter):
    for _ in range(PER_CLIENT_MAX):
        assert limiter.check("10.0.0.5") == 0.0
        limiter.record_failure("10.0.0.5")
    assert limiter.check("10.0.0.5") > 0


def test_blocking_one_caller_does_not_block_another(limiter):
    for _ in range(PER_CLIENT_MAX):
        limiter.record_failure("10.0.0.5")
    assert limiter.check("10.0.0.5") > 0
    assert limiter.check("10.0.0.6") == 0.0


def test_a_success_clears_the_history(limiter):
    """Otherwise a few typos would lock someone out for the rest of the window
    even after they got it right."""
    for _ in range(PER_CLIENT_MAX - 1):
        limiter.record_failure("10.0.0.5")
    limiter.record_success("10.0.0.5")
    for _ in range(PER_CLIENT_MAX - 1):
        assert limiter.check("10.0.0.5") == 0.0
        limiter.record_failure("10.0.0.5")


def test_the_window_expires(limiter):
    now = 1000.0
    for _ in range(PER_CLIENT_MAX):
        limiter.record_failure("10.0.0.5", now=now)
    assert limiter.check("10.0.0.5", now=now) > 0
    assert limiter.check("10.0.0.5", now=now + PER_CLIENT_WINDOW_S + 1) == 0.0


def test_retry_after_shrinks_as_the_window_passes(limiter):
    now = 1000.0
    for _ in range(PER_CLIENT_MAX):
        limiter.record_failure("10.0.0.5", now=now)
    early = limiter.check("10.0.0.5", now=now + 10)
    later = limiter.check("10.0.0.5", now=now + 200)
    assert 0 < later < early


# ------------------------------ The global counter ------------------------------

def test_rotating_the_source_address_still_hits_the_global_limit(limiter):
    """The per-address counter is only as good as the address. This is the case
    it cannot cover: a new key for every attempt."""
    for i in range(GLOBAL_MAX):
        key = f"10.0.{i // 256}.{i % 256}"
        assert limiter.check(key) == 0.0
        limiter.record_failure(key)
    assert limiter.check("10.99.99.99") > 0


def test_the_global_limit_is_well_above_ordinary_use():
    """It has to be high enough that one forgetful household never trips it,
    or the safety measure becomes the outage."""
    assert GLOBAL_MAX >= PER_CLIENT_MAX * 5


def test_tracking_is_bounded(limiter):
    """An attacker rotating addresses must not be able to grow the table
    without limit."""
    for i in range(MAX_TRACKED_CLIENTS + 500):
        limiter.record_failure(f"key-{i}")
    assert len(limiter._clients) <= MAX_TRACKED_CLIENTS


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
    request = _Req("192.168.1.40", {"x-forwarded-for": "10.0.0.1"})
    assert client_key(request) == "192.168.1.40"


def test_a_forwarded_header_from_the_local_proxy_is_used():
    """nginx runs beside the app in the same container and reaches it over the
    loopback, so this is the real client."""
    request = _Req("127.0.0.1", {"x-forwarded-for": "192.168.1.40, 127.0.0.1"})
    assert client_key(request) == "192.168.1.40"


def test_a_malformed_forwarded_header_falls_back_to_the_peer():
    request = _Req("127.0.0.1", {"x-forwarded-for": "not-an-address"})
    assert client_key(request) == "127.0.0.1"


def test_a_missing_client_does_not_raise():
    assert client_key(_Req(None)) == "unknown"


# ----------------------------- Over the real API -----------------------------

def test_login_answers_429_once_the_limit_is_reached(app_client):
    from app.core.auth import Credentials, hash_password, save_credentials
    from app.core.ratelimit import login_limiter

    save_credentials(Credentials(password_hash=hash_password("rate-limit-test-password"),
                                 must_change=False))
    login_limiter.reset()
    app_client.cookies.clear()
    try:
        for _ in range(PER_CLIENT_MAX):
            assert app_client.post("/api/v1/auth/login",
                                   json={"password": "wrong"}).status_code == 401

        blocked = app_client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0

        # And the correct password is refused too -- otherwise the throttle
        # would only slow down an attacker who already had it.
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
