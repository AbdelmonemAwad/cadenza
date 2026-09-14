"""Apple's catalogue without a MusicKit key.

A MusicKit key needs an Apple Developer Program membership, and without one
the whole Apple integration used to say "not configured". Only the user's own
library needs the key; the catalogue -- matching, artwork, track numbers,
release dates, the Apple Music link -- is answered by the iTunes Search API
with no credential at all, and with the same catalogue ids (issue #66).
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.providers.applemusic import ITUNES_RATE, AppleMusicProvider
from app.providers.base import ProviderError

pytestmark = pytest.mark.asyncio

# The shape the iTunes Search API actually returns, trimmed to what is read.
ITUNES_SEARCH = {
    "resultCount": 2,
    "results": [
        {"wrapperType": "track", "kind": "song", "artistName": "Sting",
         "collectionArtistName": None, "collectionName": "Brand New Day",
         "trackName": "Desert Rose", "trackId": 1440862563, "collectionId": 1440862401,
         "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/x/y/z/source/100x100bb.jpg",
         "releaseDate": "1999-09-27T07:00:00Z", "primaryGenreName": "Rock",
         "trackNumber": 2, "discNumber": 1, "trackCount": 10, "trackTimeMillis": 285000,
         "trackViewUrl": "https://music.apple.com/us/album/desert-rose/1440862401?i=1440862563",
         "country": "USA"},
        {"wrapperType": "track", "kind": "song", "artistName": "Somebody Else",
         "collectionName": "Covers", "trackName": "Desert Rose (Cover)", "trackId": 42,
         "collectionId": 43, "artworkUrl100": "https://x/100x100bb.jpg",
         "releaseDate": "2015-01-01T00:00:00Z", "primaryGenreName": "Pop",
         "trackNumber": 5, "discNumber": 1, "trackTimeMillis": 200000,
         "trackViewUrl": "https://music.apple.com/us/album/x/43?i=42"},
    ],
}

ITUNES_ALBUM = {
    "resultCount": 3,
    "results": [
        {"wrapperType": "collection", "collectionId": 1440862401,
         "collectionName": "Brand New Day"},
        {"wrapperType": "track", "trackId": 1, "trackName": "A Thousand Years", "trackNumber": 1,
         "trackTimeMillis": 358000, "trackViewUrl": "https://music.apple.com/x?i=1"},
        {"wrapperType": "track", "trackId": 1440862563, "trackName": "Desert Rose",
         "trackNumber": 2, "trackTimeMillis": 285000, "trackViewUrl": "https://music.apple.com/x?i=2"},
    ],
}


@pytest.fixture
def keyless(monkeypatch: pytest.MonkeyPatch) -> AppleMusicProvider:
    settings = get_settings()
    monkeypatch.setattr(settings, "apple_team_id", "")
    monkeypatch.setattr(settings, "apple_key_id", "")
    monkeypatch.setattr(settings, "apple_itunes_catalogue", True)
    return AppleMusicProvider(None)


async def test_without_a_key_the_catalogue_is_itunes_and_the_library_is_not(keyless) -> None:
    assert keyless.has_key is False
    assert keyless.catalogue == "itunes"
    assert keyless.enabled is True
    # Apple documents roughly twenty calls a minute for the key-free API.
    assert (keyless._limiter.calls, keyless._limiter.period) == ITUNES_RATE
    with pytest.raises(ProviderError, match="Developer Program"):
        keyless.developer_token()


async def test_the_switch_turns_the_key_free_catalogue_off(keyless, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "apple_itunes_catalogue", False)
    off = AppleMusicProvider(None)
    assert off.catalogue == "off" and off.enabled is False
    assert await off.lookup(title="Desert Rose", artist="Sting") == []


async def test_a_lookup_maps_the_itunes_shape_and_asks_for_full_size_artwork(
        keyless, monkeypatch) -> None:
    calls: list[tuple] = []

    async def fake_cached_json(key_parts, url, **kw):
        calls.append((url, kw.get("params")))
        return ITUNES_SEARCH

    monkeypatch.setattr(keyless, "cached_json", fake_cached_json)
    found = await keyless.lookup(title="Desert Rose", artist="Sting",
                                album="Brand New Day", duration=285.0)

    assert calls and calls[0][0] == "https://itunes.apple.com/search"
    assert calls[0][1]["country"] == keyless.storefront and calls[0][1]["entity"] == "song"

    best = found[0]
    assert best.apple_id == "1440862563", "trackId is the Apple Music song id"
    assert (best.title, best.artist, best.album) == ("Desert Rose", "Sting", "Brand New Day")
    assert (best.track_no, best.disc_no, best.year, best.date) == (2, 1, 1999, "1999-09-27")
    assert best.genre == "Rock" and best.isrc is None
    assert best.extra["url"].startswith("https://music.apple.com/")
    assert best.extra["album_id"] == "1440862401"
    px = min(3000, get_settings().artwork_target_px)
    assert best.artwork_url.endswith(f"/{px}x{px}bb.jpg"), best.artwork_url
    assert best.artwork_px == px
    assert best.confidence > found[1].confidence, "the cover ranked above the original"


async def test_an_isrc_is_not_a_route_the_key_free_catalogue_has(keyless) -> None:
    assert await keyless.by_isrc("GBAAA9900123") == []


async def test_album_tracks_come_back_in_musickit_shape(keyless, monkeypatch) -> None:
    async def fake_cached_json(key_parts, url, **kw):
        assert url == "https://itunes.apple.com/lookup" and kw["params"]["entity"] == "song"
        return ITUNES_ALBUM

    monkeypatch.setattr(keyless, "cached_json", fake_cached_json)
    tracks = await keyless.album_tracks("1440862401")
    assert [t["id"] for t in tracks] == ["1", "1440862563"], "the album row itself must be dropped"
    assert tracks[1]["attributes"] == {"name": "Desert Rose", "trackNumber": 2,
                                       "durationInMillis": 285000,
                                       "url": "https://music.apple.com/x?i=2"}


async def test_status_reports_the_catalogue_and_the_library_separately(app_client,
                                                                          monkeypatch) -> None:
    from app.core.auth import Credentials, hash_password, save_credentials

    save_credentials(Credentials(username="apple-probe", password_hash=hash_password("pw")))
    app_client.cookies.clear()
    assert app_client.post("/api/v1/auth/login",
                           json={"username": "apple-probe", "password": "pw"}).status_code == 200

    status = app_client.get("/api/v1/apple/status").json()
    assert status["configured"] is False and status["library"] is False
    assert status["catalogue"] == "itunes"

    token = app_client.get("/api/v1/apple/developer-token")
    assert token.status_code == 400 and "Developer Program" in token.json()["detail"]

    # The providers list in Settings must agree with what actually answers:
    # it showed Apple as off while the key-free catalogue was matching tracks.
    assert app_client.get("/api/v1/settings/health").json()["providers"]["applemusic"] is True
    monkeypatch.setattr(get_settings(), "apple_itunes_catalogue", False)
    assert app_client.get("/api/v1/settings/health").json()["providers"]["applemusic"] is False
