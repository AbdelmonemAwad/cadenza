from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

# Settings whose values are credentials. AcoustID and Last.fm require the key in
# the query string -- neither API accepts a header -- so the full URL of an
# outbound request contains it. Any httpx error that quotes the URL, and any
# traceback the job runner logs with exc_info, would otherwise write that key
# into cadenza.log, which lives on the config volume: a DSM shared folder, kept
# across five rotated files.
SECRET_SETTINGS = (
    "acoustid_api_key",
    "lastfm_api_key",
    "discogs_token",
)

REDACTED = "***redacted***"

# Below this length a "secret" is a placeholder or an empty string, and
# redacting it would replace unrelated substrings all over the log.
MIN_REDACTABLE = 8


def _secret_values() -> list[str]:
    # Imported here: app.config imports nothing from this module, but keeping
    # the dependency inside the call avoids ordering surprises during startup.
    from app.config import get_settings

    settings = get_settings()
    values = []
    for name in SECRET_SETTINGS:
        value = getattr(settings, name, "") or ""
        if len(value) >= MIN_REDACTABLE:
            values.append(value)
    return values


class RedactingFormatter(logging.Formatter):
    """Strips credential values out of the finished log line.

    Redaction happens after formatting rather than in a filter, so it covers
    the message, its arguments and the traceback text alike -- the URL usually
    arrives inside an exception, not in the message the caller wrote.
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in _secret_values():
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text


def setup_logging(level: str = "INFO", logfile: Path | None = None) -> None:
    fmt = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(RedactingFormatter(fmt))
    root.addHandler(console)

    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(RedactingFormatter(fmt))
        root.addHandler(handler)

    # These are chatty at INFO and drown out anything useful. httpx in
    # particular logs the full request URL at INFO, query string included.
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
