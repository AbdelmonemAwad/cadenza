"""Filesystem containment.

Every user-controlled path that reaches the filesystem goes through `contained`.
Doing it in one place is deliberate: the escapes found in issue #7 all came from
each call site inventing its own check, and one of them (`Path.relative_to`) is
purely lexical, so it *succeeded* on a path containing `..` instead of raising
and the fallback never fired.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class PathEscape(ValueError):
    """A path resolved outside the root it was required to stay within."""

    def __init__(self, candidate: Path, root: Path) -> None:
        super().__init__(f"{candidate} is outside {root}")
        self.candidate = candidate
        self.root = root


def contained(candidate: str | Path, root: str | Path, *,
              must_exist: bool = False) -> Path:
    """Resolve `candidate` and assert it is inside `root`.

    `resolve()` first, and that is the whole point: it collapses `..`, follows
    symlinks and produces an absolute path, so the comparison is about where
    the path actually lands rather than how it is spelled. A purely textual
    check passes `/music/../etc/passwd` and a `..`-free symlink alike.

    `strict=False` so a destination that does not exist yet can still be
    validated before it is created.
    """
    root_path = Path(root).resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)

    if resolved != root_path and root_path not in resolved.parents:
        raise PathEscape(resolved, root_path)
    if must_exist and not resolved.exists():
        raise PathEscape(resolved, root_path)
    return resolved


def is_contained(candidate: str | Path, root: str | Path) -> bool:
    try:
        contained(candidate, root)
    except PathEscape:
        return False
    return True
