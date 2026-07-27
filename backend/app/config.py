"""Application settings, layered: environment -> /config/settings.json (UI-editable)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.secretfile import tighten, write_private_text

APP_NAME = "Cadenza"
APP_VERSION = "1.0.0"

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3", ".flac", ".wav", ".aac", ".m4a", ".m4b", ".alac",
    ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif", ".ape", ".wv", ".dsf",
})

LOSSLESS_CODECS: frozenset[str] = frozenset({
    "flac", "alac", "wav", "pcm_s16le", "pcm_s24le", "aiff", "ape", "wavpack", "dsd",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CADENZA_", env_file=".env", extra="ignore")

    # --- Paths ---
    music_root: Path = Path("/music")
    quarantine_root: Path = Path("/quarantine")
    config_dir: Path = Path("/config")

    # --- Runtime ---
    log_level: str = "INFO"
    workers: int = 4
    http_port: int = 8760
    api_prefix: str = "/api/v1"
    default_locale: str = "en"
    # Off in production: the OpenAPI schema is a map of every destructive
    # endpoint and its exact request shape.
    enable_docs: bool = False
    # Empty in production: nginx serves the UI from the same origin, so no
    # cross-origin request should ever be allowed to carry the session cookie.
    # Set to the Vite dev server, e.g.
    # CADENZA_CORS_DEV_ORIGINS='["http://localhost:5173"]', only while
    # developing. Exact origins -- no wildcards and no port patterns.
    # NoDecode is load-bearing. Without it pydantic-settings JSON-decodes any
    # complex field inside the environment source, before validators run, so
    # the two spellings anyone reaches for first -- CADENZA_CORS_DEV_ORIGINS=
    # to mean "none", and a bare http://localhost:5173 to mean one origin --
    # both aborted startup with a SettingsError, thrown before logging is even
    # configured. A setting that ships off has to be switchable without the
    # user knowing it is JSON underneath.
    cors_dev_origins: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("cors_dev_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept a comma-separated list, a JSON array, or nothing at all."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return ()
        if text.startswith("["):
            return tuple(json.loads(text))
        return tuple(part.strip() for part in text.split(",") if part.strip())

    # --- Scanning ---
    follow_symlinks: bool = False
    skip_hidden: bool = True
    min_file_bytes: int = 32 * 1024          # anything smaller is treated as truncated
    hash_chunk_bytes: int = 4 * 1024 * 1024

    # --- Deduplication ---
    acoustic_enabled: bool = True
    fingerprint_duration_s: int = 120        # seconds of audio fed to the fingerprinter
    acoustic_match_threshold: float = 0.90   # see fingerprint.similarity for the 0.5 baseline
    duration_tolerance_s: float = 7.0        # allowed length delta inside a cluster
    title_fuzzy_threshold: int = 88          # rapidfuzz score 0..100

    # --- Path templates ---
    path_template: str = "{albumartist}/{year} - {album}/{track:02d} - {title}"
    compilation_template: str = "Compilations/{year} - {album}/{track:02d} - {artist} - {title}"
    single_template: str = "{albumartist}/Singles/{title}"
    replace_unsafe_with: str = "_"
    max_path_component: int = 120

    # --- Artwork & lyrics ---
    embed_artwork: bool = True
    write_cover_file: bool = True
    cover_filename: str = "cover.jpg"
    artwork_min_px: int = 600
    artwork_target_px: int = 1400
    embed_lyrics: bool = True
    write_lrc_file: bool = True

    # --- Transcoding ---
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    fpcalc_bin: str = "fpcalc"
    transcode_threads: int = 2

    # --- Metadata providers ---
    acoustid_api_key: str = ""
    discogs_token: str = ""
    lastfm_api_key: str = ""
    musicbrainz_contact: str = "admin@example.com"
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: Path = Path("/config/AuthKey.p8")
    apple_storefront: str = "us"

    provider_order: list[str] = Field(
        default_factory=lambda: ["musicbrainz", "applemusic", "discogs", "lastfm"]
    )

    # --- Safety ---
    dry_run_default: bool = True
    hard_delete_allowed: bool = False        # never permanently delete unless opted in
    quarantine_retention_days: int = 30

    user_agent: str = f"{APP_NAME}/{APP_VERSION}"

    @property
    def db_path(self) -> Path:
        return self.config_dir / "cadenza.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def cache_dir(self) -> Path:
        return self.config_dir / "cache"

    @property
    def artwork_cache(self) -> Path:
        return self.cache_dir / "artwork"

    @property
    def overrides_file(self) -> Path:
        return self.config_dir / "settings.json"

    def ua_header(self) -> str:
        """MusicBrainz requires a contact address in the User-Agent."""
        return f"{self.user_agent} ( {self.musicbrainz_contact} )"

    def ensure_dirs(self) -> None:
        for p in (self.config_dir, self.cache_dir, self.artwork_cache,
                  self.config_dir / "logs", self.quarantine_root):
            p.mkdir(parents=True, exist_ok=True)

    def tighten_secret_files(self) -> None:
        """Bring credential files down to 0600 on startup.

        New writes are already private. This is for files an earlier version
        created at the process umask, and for a config volume restored from a
        backup that did not preserve modes -- neither of which the user has any
        way to notice.
        """
        for name in ("settings.json", "auth.json", "secret_key",
                     "apple_user_token.json", "initial-password.txt"):
            tighten(self.config_dir / name)
        tighten(Path(self.apple_private_key_path))


_lock = threading.RLock()
_instance: Settings | None = None


def _load_overrides(base: Settings) -> Settings:
    """Merge UI-saved overrides on top of environment values."""
    f = base.overrides_file
    if not f.is_file():
        return base
    try:
        data: dict[str, Any] = json.loads(f.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    known = set(Settings.model_fields)
    merged = base.model_dump()
    merged.update({k: v for k, v in data.items() if k in known})
    return Settings(**merged)


def get_settings() -> Settings:
    global _instance
    with _lock:
        if _instance is None:
            _instance = _load_overrides(Settings())
        return _instance


def save_settings(patch: dict[str, Any]) -> Settings:
    """Persist a UI settings patch and reload the cached instance."""
    global _instance
    with _lock:
        s = get_settings()
        f = s.overrides_file
        current: dict[str, Any] = {}
        if f.is_file():
            try:
                current = json.loads(f.read_text("utf-8"))
            except json.JSONDecodeError:
                current = {}
        known = set(Settings.model_fields)
        current.update({k: v for k, v in patch.items() if k in known})
        # This file holds the AcoustID, Discogs and Last.fm keys, so it is
        # written 0600 from the moment it exists. It used to be created at the
        # process umask on a config volume that is a DSM shared folder, where
        # other packages and users on the NAS can read it.
        write_private_text(
            f, json.dumps(current, ensure_ascii=False, indent=2, default=str))
        _instance = _load_overrides(Settings())
        return _instance
