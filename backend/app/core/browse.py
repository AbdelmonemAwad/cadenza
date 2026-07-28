"""Choosing a folder from the interface, without handing out the filesystem.

The Settings page needs a folder picker: for the library paths, and for the
data folder. Typing an absolute path into a text box is how it worked before,
which on a NAS means the user goes to File Station, reads a path, and copies it
across by hand -- and a typo produces a folder that silently does not exist.

A browse endpoint is an information-disclosure surface, so this is deliberately
narrow:

  * Directories only. File names are never returned, so nothing here can be
    used to enumerate someone's documents.
  * Rooted. A path is refused unless it resolves inside one of the roots below,
    using the same `contained` primitive as every other path in the app --
    resolved first, so `..` and symlinks are followed to where they actually
    land rather than compared as text.
  * Denied prefixes on top of that, because a volume root can contain a
    system-ish directory and there is no reason to walk into one.

The roots are the DSM volumes, plus whatever this deployment was configured
with -- in a container there are no /volumeN mounts, and the only meaningful
places are the paths that were mounted in.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import Settings
from app.core.paths import PathEscape, contained

log = logging.getLogger(__name__)

# Never browsable, even when a root would otherwise allow it.
DENIED = (
    "/etc", "/proc", "/sys", "/dev", "/run", "/boot", "/usr", "/bin", "/sbin",
    "/lib", "/lib64", "/root", "/var/packages", "/var/services/homes",
    "/volume1/@", "/volumeUSB",
)

# A directory with more children than this is listed up to the cap. Synology
# shared folders routinely hold tens of thousands of entries and the picker
# only ever shows one page.
MAX_ENTRIES = 500


def browsable_roots(settings: Settings) -> list[Path]:
    """Where a folder picker may start, most useful first."""
    found: list[Path] = []

    def add(candidate: Path) -> None:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            return
        if resolved.is_dir() and resolved not in found and not _denied(resolved):
            found.append(resolved)

    # What this deployment actually uses comes first: on a container these are
    # the only real answers, and on a NAS they are the ones the user wants.
    add(Path(settings.music_root))
    add(Path(settings.config_dir))

    # DSM volumes. glob rather than a fixed list: a NAS can have any number,
    # and a DS1821+ with an expansion unit has more than most.
    try:
        for volume in sorted(Path("/").glob("volume[0-9]*")):
            add(volume)
    except OSError:          # pragma: no cover -- a filesystem that refuses /
        log.debug("could not enumerate volumes", exc_info=True)

    return found


def _denied(path: Path) -> bool:
    # as_posix, not str: on Windows -- where the test suite runs -- str() gives
    # backslashes and every one of these prefixes silently stops matching, so
    # the guard would look present and test green while denying nothing.
    text = path.as_posix()
    return any(text == d or text.startswith(d + "/") or text.startswith(d)
               for d in DENIED)


def resolve_within_roots(candidate: str | Path, settings: Settings) -> Path:
    """Resolve `candidate` and assert it is inside a browsable root.

    Raises PathEscape, which the endpoint turns into a 400. Refusing is the
    default: a path that matches no root at all is refused rather than mapped
    onto the first one.
    """
    roots = browsable_roots(settings)
    for root in roots:
        try:
            resolved = contained(candidate, root, must_exist=True)
        except PathEscape:
            continue
        if _denied(resolved):
            raise PathEscape(resolved, root)
        return resolved
    raise PathEscape(Path(str(candidate)), roots[0] if roots else Path("/"))


def list_directories(path: Path) -> list[dict]:
    """The immediate subdirectories of `path`. Never files.

    Unreadable children are listed rather than hidden, marked so the interface
    can grey them out -- on a NAS "I can see it but cannot enter it" is the
    normal state of a share the service account has not been granted, and
    hiding it would make the one thing the user has to fix invisible.
    """
    entries: list[dict] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if len(entries) >= MAX_ENTRIES:
                    break
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                child = Path(entry.path)
                if _denied(child):
                    continue
                entries.append({
                    "name": entry.name,
                    "path": str(child),
                    "readable": _readable(child),
                })
    except (OSError, PermissionError) as exc:
        raise PathEscape(path, path) from exc

    entries.sort(key=lambda e: e["name"].lower())
    return entries


def list_files(path: Path, suffixes: tuple[str, ...]) -> list[dict]:
    """Files directly in `path` whose suffix is one of `suffixes`.

    Only ever called with an explicit, non-empty extension list. That is the
    whole reason this is separate from `list_directories`: a general file
    listing would let the picker enumerate a user's documents, while "show me
    the .p8 files in this folder" discloses only what the user is already
    looking for.
    """
    if not suffixes:
        return []
    wanted = tuple(s.lower() for s in suffixes)
    entries: list[dict] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if len(entries) >= MAX_ENTRIES:
                    break
                if entry.name.startswith("."):
                    continue
                if not entry.name.lower().endswith(wanted):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    size = entry.stat().st_size
                except OSError:
                    continue
                entries.append({"name": entry.name, "path": entry.path, "size": size})
    except (OSError, PermissionError) as exc:
        raise PathEscape(path, path) from exc

    entries.sort(key=lambda e: e["name"].lower())
    return entries


def _readable(path: Path) -> bool:
    try:
        with os.scandir(path) as it:
            next(iter(it), None)
        return True
    except (OSError, PermissionError):
        return False
