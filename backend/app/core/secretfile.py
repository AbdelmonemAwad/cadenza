"""Writing files that hold credentials.

This lived inside `auth.py` and was used only for the password file, while the
other three credential files in the config volume -- `settings.json` with the
provider API keys, the Apple Music user token, and the signing key -- were
written with plain `write_text` at the process umask. Two of them then called
`chmod` afterwards, which still leaves the contents readable for the moment
between the create and the chmod.

That window is not theoretical here. The config volume is a DSM shared folder,
its default permissions are set by whoever created it, and other packages and
users on the NAS can read it. So there is one implementation and every secret
goes through it.
"""
from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

SECRET_MODE = 0o600


def write_private(path: Path, data: bytes) -> None:
    """Write a file that is never even briefly readable by anyone else.

    Creating then chmod-ing leaves a window at the process umask, which matters
    on a NAS where the config volume is usually a shared folder. O_EXCL on a
    unique temp name means the mode argument is honoured rather than silently
    ignored for a pre-existing file, and fchmod covers a permissive umask.

    The rename at the end is atomic, so a reader sees either the old file or
    the complete new one, never a half-written credential.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
    try:
        try:
            # Belt and braces against a permissive umask. POSIX-only, and
            # absent on Windows where developers run the test suite; the
            # O_EXCL create mode above already carries the permission on the
            # platform that ships.
            if hasattr(os, "fchmod"):
                os.fchmod(fd, SECRET_MODE)
            os.write(fd, data)
            # Survive a NAS power cut: without this the rename can land while
            # the contents are still in the page cache, leaving an empty
            # credential file that silently re-bootstraps a new password.
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        # A failed write must not leave the credential behind under a name
        # nothing knows about. The temp name carries a random suffix, so the
        # app's own cleanup paths -- DELETE /apple/link, removing
        # initial-password.txt, tighten_secret_files -- all match literal
        # filenames and would never find it. It would sit on the shared folder
        # permanently, and hidden from File Station's default view at that.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_private_text(path: Path, text: str) -> None:
    write_private(path, text.encode("utf-8"))


def tighten(path: Path) -> None:
    """Bring an existing file down to 0600.

    For files written by an earlier version, or restored from a backup that did
    not preserve modes. Silent when the filesystem does not support it -- some
    do not, and refusing to start over a chmod would be worse than the exposure
    it is guarding against.
    """
    try:
        if path.is_file() and (path.stat().st_mode & 0o777) != SECRET_MODE:
            path.chmod(SECRET_MODE)
    except OSError:
        pass
