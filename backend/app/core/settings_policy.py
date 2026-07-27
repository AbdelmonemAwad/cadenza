"""Which settings may be changed over the API, and what values are acceptable.

Before this existed, `PATCH /settings` accepted any field present in the model
and validated only that the *name* was known. That made it an arbitrary-write
primitive (issue #6):

  * `ffmpeg_bin` / `ffprobe_bin` / `fpcalc_bin` are argv[0] of every subprocess,
    so writing them is remote code execution inside the container.
  * `cover_filename` is joined onto a track's directory and the result is
    returned verbatim by the artwork endpoint. `pathlib` discards the left side
    of a join when the right side is absolute, so `/config/settings.json`
    turned that endpoint into an arbitrary file read.
  * `music_root`, `quarantine_root` and `config_dir` relocate every filesystem
    operation the app performs.
  * `replace_unsafe_with` is substituted into path components by `sanitize`, so
    a value containing a separator turns the sanitiser into a traversal
    primitive.

The allowlist is therefore positive: a field is remotely writable only if it is
named here. A new setting is locked down by default rather than by remembering
to exclude it.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Deployment-level configuration. Set through the environment or the compose
# file, never over the network.
LOCKED_FIELDS: frozenset[str] = frozenset({
    "music_root", "quarantine_root", "config_dir",
    "ffmpeg_bin", "ffprobe_bin", "fpcalc_bin",
    "apple_private_key_path",
    "http_port", "api_prefix", "workers", "enable_docs",
    "user_agent", "provider_order",
})

_SAFE_REPLACEMENT = re.compile(r"^[A-Za-z0-9 _\-.,()\[\]]{1,4}$")
_BARE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{0,63}$")


class SettingRejected(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _bounded(low: float, high: float) -> Callable[[str, Any], Any]:
    def check(field: str, value: Any) -> Any:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise SettingRejected(field, "must be a number") from exc
        if not low <= number <= high:
            raise SettingRejected(field, f"must be between {low} and {high}")
        return int(number) if float(number).is_integer() and isinstance(value, int) else number
    return check


def _cover_filename(field: str, value: Any) -> str:
    text = str(value)
    # A bare filename only. Anything with a separator -- or an absolute path,
    # which pathlib would let swallow the directory it is joined to -- is what
    # made this an arbitrary file read.
    if "/" in text or "\\" in text or text in (".", "..") or "\x00" in text:
        raise SettingRejected(field, "must be a bare filename with no path separator")
    if not _BARE_FILENAME.match(text):
        raise SettingRejected(field, "contains characters that are not allowed")
    return text


def _replacement(field: str, value: Any) -> str:
    text = str(value)
    if not _SAFE_REPLACEMENT.match(text):
        raise SettingRejected(
            field, "must be 1-4 characters and cannot contain a path separator")
    return text


def _template(field: str, value: Any) -> str:
    text = str(value)
    if len(text) > 400:
        raise SettingRejected(field, "is too long")
    # A template is expanded into path components. `..` and absolute forms
    # would escape the library root once the result is joined.
    if text.startswith(("/", "\\")) or ".." in text or "\x00" in text:
        raise SettingRejected(field, "cannot be absolute or contain '..'")
    if "\\" in text:
        raise SettingRejected(field, "use '/' as the separator")
    return text


def _plain_text(limit: int) -> Callable[[str, Any], str]:
    def check(field: str, value: Any) -> str:
        text = str(value)
        if len(text) > limit:
            raise SettingRejected(field, f"must be at most {limit} characters")
        if "\x00" in text or "\n" in text or "\r" in text:
            raise SettingRejected(field, "cannot contain control characters")
        return text
    return check


def _boolean(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise SettingRejected(field, "must be true or false")


def _locale(field: str, value: Any) -> str:
    text = str(value)
    if text not in ("en", "ar"):
        raise SettingRejected(field, "must be 'en' or 'ar'")
    return text


def _log_level(field: str, value: Any) -> str:
    text = str(value).upper()
    if text not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise SettingRejected(field, "is not a valid log level")
    return text


# field -> validator. Presence in this mapping is what makes a field writable.
WRITABLE_FIELDS: dict[str, Callable[[str, Any], Any]] = {
    # Safety
    "dry_run_default": _boolean,
    "hard_delete_allowed": _boolean,
    # A retention of 0 would make every future quarantine immediately purgeable,
    # which quietly removes the safety net the whole design rests on.
    "quarantine_retention_days": _bounded(1, 3650),

    # Duplicate engine
    "acoustic_enabled": _boolean,
    # Below ~0.85 the threshold sits near the 0.5 Hamming baseline for unrelated
    # audio and starts matching different songs.
    "acoustic_match_threshold": _bounded(0.85, 0.999),
    "duration_tolerance_s": _bounded(0, 120),
    "title_fuzzy_threshold": _bounded(50, 100),

    # Scanning
    "follow_symlinks": _boolean,
    "skip_hidden": _boolean,
    "min_file_bytes": _bounded(0, 100 * 1024 * 1024),

    # Organisation
    "path_template": _template,
    "compilation_template": _template,
    "single_template": _template,
    "replace_unsafe_with": _replacement,
    "max_path_component": _bounded(8, 255),

    # Artwork and lyrics
    "embed_artwork": _boolean,
    "write_cover_file": _boolean,
    "cover_filename": _cover_filename,
    "artwork_min_px": _bounded(0, 10000),
    "artwork_target_px": _bounded(64, 10000),
    "embed_lyrics": _boolean,
    "write_lrc_file": _boolean,

    # Transcoding
    "transcode_threads": _bounded(1, 32),

    # Providers
    "acoustid_api_key": _plain_text(200),
    "discogs_token": _plain_text(200),
    "lastfm_api_key": _plain_text(200),
    "musicbrainz_contact": _plain_text(200),
    "apple_team_id": _plain_text(64),
    "apple_key_id": _plain_text(64),
    "apple_storefront": _plain_text(8),

    # Runtime
    "log_level": _log_level,
    "default_locale": _locale,
}


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Return the cleaned patch, or raise SettingRejected on the first problem."""
    cleaned: dict[str, Any] = {}
    for field, value in patch.items():
        if field in LOCKED_FIELDS:
            raise SettingRejected(
                field, "is deployment configuration and cannot be changed over the API")
        validator = WRITABLE_FIELDS.get(field)
        if validator is None:
            raise SettingRejected(field, "is not a known writable setting")
        cleaned[field] = validator(field, value)
    return cleaned
