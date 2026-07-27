from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(level: str = "INFO", logfile: Path | None = None) -> None:
    fmt = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)

    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)

    # These are chatty at INFO and drown out anything useful.
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
