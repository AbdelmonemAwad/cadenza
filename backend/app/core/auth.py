"""Password storage and session tokens.

Deliberately built on the standard library only. `hashlib.scrypt` is a memory-hard
KDF, and `hmac`/`secrets` cover signing and constant-time comparison, so adding
passlib or python-jose would widen the dependency surface for no gain.

Sessions are stateless signed tokens rather than server-side records: the app is
a single process on a NAS, and a signed token survives a container restart
without needing a session store. Revocation is handled by an epoch counter kept
with the credentials -- bumping it invalidates every token issued before it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

# scrypt parameters. n=2**15 costs roughly 100 ms and 32 MB per verification
# here: slow enough to make offline cracking expensive, fast enough that a
# sign-in does not feel stalled.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

# scrypt needs 128 * N * r bytes = exactly 32 MiB at these parameters, which is
# also OpenSSL's default maxmem, so it fails with "memory limit exceeded"
# unless the ceiling is raised explicitly.
SCRYPT_MAXMEM = 128 * 1024 * 1024

# Bounds applied when parsing a STORED hash. Without them a tampered auth.json
# could name an enormous cost and turn every sign-in attempt into a memory and
# CPU exhaustion primitive.
MAX_STORED_N = 2 ** 20
MAX_STORED_R = 32
MAX_STORED_P = 16

SESSION_TTL_SECONDS = 60 * 60 * 24 * 14      # 14 days
MIN_PASSWORD_LENGTH = 10

# Subject of a session issued while the generated first-run password is still
# in force. current_user refuses it everywhere except the password endpoints,
# so the forced change cannot be skipped by ignoring the UI.
SUBJECT_ADMIN = "admin"
SUBJECT_PASSWORD_RESET = "pwreset"

AUTH_FILE = "auth.json"
SECRET_FILE = "secret_key"
INITIAL_PASSWORD_FILE = "initial-password.txt"


class AuthError(Exception):
    pass


# --------------------------------- Passwords ---------------------------------

def hash_password(password: str) -> str:
    """Return a self-describing hash so the parameters can change later."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    ])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        if not (0 < n <= MAX_STORED_N and 0 < r <= MAX_STORED_R and 0 < p <= MAX_STORED_P):
            log.warning("stored hash declares out-of-range scrypt cost; rejecting")
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                n=n, r=r, p=p,
                                dklen=len(expected), maxmem=SCRYPT_MAXMEM)
    except (ValueError, TypeError, binascii.Error):
        return False
    # Constant time: a timing side channel here would leak the digest byte by byte.
    return hmac.compare_digest(actual, expected)


# ------------------------------- Signing key --------------------------------

def _secret_key() -> bytes:
    """Load the session signing key, generating it on first use.

    Stored as hex rather than raw bytes. The raw key was read back with
    .strip(), which silently removed leading or trailing whitespace bytes --
    and roughly one generated key in twenty starts or ends with
    0x09/0x0a/0x0d/0x20, so the key loaded back differed from the one used to
    sign and the first session of those installs was unverifiable.
    """
    path = get_settings().config_dir / SECRET_FILE
    if path.is_file():
        try:
            key = bytes.fromhex(path.read_text("ascii").strip())
            if len(key) >= 32:
                return key
            log.warning("session key file is too short; regenerating")
        except (OSError, ValueError):
            log.warning("session key file is unreadable or malformed; regenerating")

    key = secrets.token_bytes(48)
    _write_private(path, key.hex().encode("ascii"))
    return key


def _write_private(path: Path, data: bytes) -> None:
    """Write a file that is never even briefly readable by anyone else.

    Creating then chmod-ing leaves a window at the process umask, which matters
    on a NAS where the config volume is usually a shared folder. O_EXCL on a
    unique temp name means the mode argument is honoured rather than silently
    ignored for a pre-existing file, and fchmod covers a permissive umask.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # Belt and braces against a permissive umask. POSIX-only, and absent on
        # Windows where developers run the test suite; the O_EXCL create mode
        # above already carries the permission on the platform that ships.
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        os.write(fd, data)
        # Survive a NAS power cut: without this the rename can land while the
        # contents are still in the page cache, leaving an empty credential
        # file that silently re-bootstraps a new password.
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.debug("could not remove %s", path)


# --------------------------------- Sessions ---------------------------------

def issue_session(subject: str = SUBJECT_ADMIN, ttl: int = SESSION_TTL_SECONDS,
                  epoch: int | None = None) -> str:
    if epoch is None:
        creds = load_credentials()
        epoch = creds.session_epoch if creds else 0
    payload = json.dumps({"sub": subject, "exp": int(time.time()) + ttl, "ep": epoch},
                         separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_secret_key(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def verify_session(token: str | None) -> str | None:
    """Return the subject of a valid, unexpired, unrevoked token, else None."""
    if not token or token.count(".") != 1:
        # Exactly one separator: anything else is non-canonical and rejected
        # rather than silently reinterpreted.
        return None
    body, _, signature = token.partition(".")
    if not body or not signature:
        return None
    try:
        expected = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
        given = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except (ValueError, TypeError, UnicodeEncodeError, binascii.Error):
        return None
    if not hmac.compare_digest(given, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        expires = int(payload["exp"])
        epoch = int(payload.get("ep", 0))
        subject = payload["sub"]
    except (ValueError, TypeError, KeyError, binascii.Error):
        return None
    if expires < time.time() or not isinstance(subject, str):
        return None

    # Revocation: an epoch bump invalidates every token issued before it.
    creds = load_credentials()
    if creds is not None and epoch != creds.session_epoch:
        return None
    return subject


# ------------------------------- Credentials --------------------------------

@dataclass(slots=True)
class Credentials:
    password_hash: str
    must_change: bool = False
    session_epoch: int = 0


def _auth_path() -> Path:
    return get_settings().config_dir / AUTH_FILE


def load_credentials() -> Credentials | None:
    path = _auth_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        return Credentials(
            password_hash=data["password_hash"],
            must_change=bool(data.get("must_change", False)),
            session_epoch=int(data.get("session_epoch", 0)),
        )
    except (OSError, ValueError, KeyError):
        log.warning("auth.json is unreadable or malformed; treating as unconfigured")
        return None


def save_credentials(creds: Credentials) -> None:
    _write_private(_auth_path(), json.dumps({
        "password_hash": creds.password_hash,
        "must_change": creds.must_change,
        "session_epoch": creds.session_epoch,
    }, separators=(",", ":")).encode())

    # Once a real password is set the generated one is no longer a credential
    # and must not linger on the config volume.
    if not creds.must_change:
        _remove_quietly(get_settings().config_dir / INITIAL_PASSWORD_FILE)


def set_password(new_password: str) -> Credentials:
    """Store a new password and invalidate every existing session."""
    previous = load_credentials()
    creds = Credentials(
        password_hash=hash_password(new_password),
        must_change=False,
        # The epoch bump is what makes a password change actually end other
        # sessions, instead of leaving them valid for the rest of their TTL.
        session_epoch=(previous.session_epoch + 1) if previous else 1,
    )
    save_credentials(creds)
    return creds


def revoke_all_sessions() -> None:
    creds = load_credentials()
    if creds is None:
        return
    creds.session_epoch += 1
    save_credentials(creds)


def ensure_initialised() -> str | None:
    """Create a random first-run password if none is configured.

    Returns the generated password so the caller can surface it once, or None
    when credentials already exist. A fixed default would be worse than no
    authentication: it looks protected while every install shares one credential.
    """
    if load_credentials() is not None:
        return None

    password = secrets.token_urlsafe(15)
    save_credentials(Credentials(password_hash=hash_password(password),
                                 must_change=True, session_epoch=0))

    # Written 0600 into the config volume because NAS users routinely cannot
    # read container logs and would otherwise be locked out of their own
    # install. Deleted automatically once a real password is set.
    _write_private(get_settings().config_dir / INITIAL_PASSWORD_FILE, (
        "Cadenza initial password\n"
        "========================\n\n"
        f"    {password}\n\n"
        "Sign in and change it. This file is removed automatically once you do.\n"
    ).encode())
    return password
