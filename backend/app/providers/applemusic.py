"""Apple Music API (MusicKit): ES256 developer token plus an optional user token.

- The developer token is a JWT signed with an Apple .p8 key, valid for up to six
  months. It is enough for catalogue search and library matching.
- The Music-User-Token comes from MusicKit JS in the browser (the UI handles
  that flow) and is only required for personal library and playlist access.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import jwt
from rapidfuzz import fuzz

from app.providers.base import BaseProvider, ProviderError, TrackMetadata

log = logging.getLogger(__name__)

API = "https://api.music.apple.com/v1"
TOKEN_TTL = 60 * 60 * 24 * 150       # 150 days; Apple's ceiling is 180


class AppleMusicProvider(BaseProvider):
    name = "applemusic"
    rate_calls = 10
    rate_period = 1.0
    cache_ttl_days = 30

    def __init__(self, *args, user_token: str | None = None, **kw) -> None:
        super().__init__(*args, **kw)
        self.user_token = user_token
        self._dev_token: str | None = None
        self._dev_exp: float = 0.0

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.apple_team_id and s.apple_key_id
                    and Path(s.apple_private_key_path).is_file())

    # ---- Tokens ----

    def developer_token(self) -> str:
        now = time.time()
        if self._dev_token and now < self._dev_exp - 3600:
            return self._dev_token
        s = self.settings
        key_path = Path(s.apple_private_key_path)
        if not key_path.is_file():
            raise ProviderError(f"Apple private key not found: {key_path}")
        exp = int(now) + TOKEN_TTL
        self._dev_token = jwt.encode(
            {"iss": s.apple_team_id, "iat": int(now), "exp": exp},
            key_path.read_text("utf-8"),
            algorithm="ES256",
            headers={"alg": "ES256", "kid": s.apple_key_id},
        )
        self._dev_exp = exp
        return self._dev_token

    def _headers(self, with_user: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.developer_token()}"}
        if with_user:
            if not self.user_token:
                raise ProviderError("Music-User-Token required: link the account first")
            headers["Music-User-Token"] = self.user_token
        return headers

    @property
    def storefront(self) -> str:
        return self.settings.apple_storefront or "us"

    # ---- Catalogue search and matching ----

    async def lookup(self, *, title=None, artist=None, album=None, duration=None,
                     isrc=None, **_) -> list[TrackMetadata]:
        if not self.enabled:
            return []
        if isrc:
            found = await self.by_isrc(isrc)
            if found:
                return found
        if not title:
            return []

        term = " ".join(x for x in (artist, title) if x)
        data = await self.cached_json(
            ("search", self.storefront, term),
            f"{API}/catalog/{self.storefront}/search",
            params={"term": term, "types": "songs", "limit": 10},
            headers=self._headers(),
        )
        songs = (((data or {}).get("results") or {}).get("songs") or {}).get("data") or []
        out = [self._from_song(s) for s in songs]
        for md in out:
            md.confidence = self._confidence(md, title, artist, album, duration)
        out.sort(key=lambda m: m.confidence, reverse=True)
        return out

    async def by_isrc(self, isrc: str) -> list[TrackMetadata]:
        data = await self.cached_json(
            ("isrc", self.storefront, isrc),
            f"{API}/catalog/{self.storefront}/songs",
            params={"filter[isrc]": isrc},
            headers=self._headers(),
        )
        out = []
        for song in (data or {}).get("data") or []:
            md = self._from_song(song)
            md.confidence = 0.99
            out.append(md)
        return out

    # ---- Library and playlists (require a Music-User-Token) ----

    async def list_playlists(self, limit: int = 100) -> list[dict]:
        out, offset = [], 0
        page = min(limit, 100)
        while True:
            data = await self.get_json(
                f"{API}/me/library/playlists",
                params={"limit": page, "offset": offset},
                headers=self._headers(with_user=True),
            )
            items = (data or {}).get("data") or []
            out.extend(items)
            if len(items) < page:
                break
            offset += len(items)
        return out

    async def playlist_tracks(self, playlist_id: str) -> list[dict]:
        out, offset = [], 0
        while True:
            data = await self.get_json(
                f"{API}/me/library/playlists/{playlist_id}/tracks",
                params={"limit": 100, "offset": offset},
                headers=self._headers(with_user=True),
            )
            items = (data or {}).get("data") or []
            out.extend(items)
            if len(items) < 100:
                break
            offset += len(items)
        return out

    async def create_playlist(self, name: str, description: str,
                              catalog_ids: list[str]) -> dict | None:
        payload = {
            "attributes": {"name": name, "description": description},
            "relationships": {
                "tracks": {"data": [{"id": cid, "type": "songs"} for cid in catalog_ids]}
            },
        }
        r = await self._request(
            "POST", f"{API}/me/library/playlists",
            json=payload, headers=self._headers(with_user=True),
        )
        return r.json() if r.status_code < 400 else None

    async def album_tracks(self, album_id: str) -> list[dict]:
        data = await self.cached_json(
            ("album", self.storefront, album_id),
            f"{API}/catalog/{self.storefront}/albums/{album_id}",
            params={"include": "tracks"}, headers=self._headers(),
        )
        albums = (data or {}).get("data") or []
        if not albums:
            return []
        return ((albums[0].get("relationships") or {}).get("tracks") or {}).get("data") or []

    # ---- Conversion ----

    def _from_song(self, song: dict) -> TrackMetadata:
        attrs = song.get("attributes") or {}
        artwork = attrs.get("artwork") or {}
        art_url = px = None
        if artwork.get("url"):
            px = min(int(artwork.get("width") or 3000), self.settings.artwork_target_px)
            art_url = artwork["url"].replace("{w}", str(px)).replace("{h}", str(px))

        release = attrs.get("releaseDate") or ""
        return TrackMetadata(
            source=self.name,
            title=attrs.get("name"),
            artist=attrs.get("artistName"),
            albumartist=attrs.get("artistName"),
            album=attrs.get("albumName"),
            date=release or None,
            year=int(release[:4]) if release[:4].isdigit() else None,
            track_no=attrs.get("trackNumber"),
            disc_no=attrs.get("discNumber"),
            genre=(attrs.get("genreNames") or [None])[0],
            composer=attrs.get("composerName"),
            isrc=attrs.get("isrc"),
            apple_id=song.get("id"),
            artwork_url=art_url,
            artwork_px=px,
            extra={
                "url": attrs.get("url"),
                "has_lyrics": attrs.get("hasLyrics"),
                "duration_ms": attrs.get("durationInMillis"),
                "content_rating": attrs.get("contentRating"),
            },
        )

    @staticmethod
    def _confidence(md: TrackMetadata, title: str | None, artist: str | None,
                    album: str | None, duration: float | None) -> float:
        """Apple ranks by popularity, not similarity, so we score the match ourselves."""
        from app.core.dedup import normalize_text

        score = 0.0
        if title and md.title:
            score += 0.45 * fuzz.token_sort_ratio(
                normalize_text(title), normalize_text(md.title)) / 100
        if artist and md.artist:
            score += 0.30 * fuzz.token_set_ratio(
                normalize_text(artist), normalize_text(md.artist)) / 100
        elif not artist:
            score += 0.15
        if album and md.album:
            score += 0.15 * fuzz.token_sort_ratio(
                normalize_text(album), normalize_text(md.album)) / 100
        elif not album:
            score += 0.07
        duration_ms = (md.extra or {}).get("duration_ms")
        if duration and duration_ms:
            score += 0.10 if abs(duration_ms / 1000 - duration) <= 3 else 0.0
        else:
            score += 0.04
        return round(min(1.0, score), 4)
