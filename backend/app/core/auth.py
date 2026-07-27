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
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core.secretfile import write_private

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

SUBJECT_ADMIN = "admin"
# Retained so sessions issued by an older build are still recognised and can be
# refused deliberately, rather than being silently treated as full sessions.
SUBJECT_PASSWORD_RESET = "pwreset"

AUTH_FILE = "auth.json"
SECRET_FILE = "secret_key"
# No longer written. Kept so an upgrade from an older install can delete the
# file that build left behind.
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
    write_private(path, key.hex().encode("ascii"))
    return key


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
    # Chosen by the user during first-run setup, together with the password.
    # Cadenza used to generate a random password, write it to a file on the
    # config volume and force a change at first sign-in. That handed the user a
    # credential they never asked for and had to go and find, and it meant a
    # secret sat on disk until they got around to changing it.
    username: str = "admin"
    password_hash: str = ""
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
            username=str(data.get("username") or "admin"),
            session_epoch=int(data.get("session_epoch", 0)),
        )
    except (OSError, ValueError, KeyError):
        log.warning("auth.json is unreadable or malformed; treating as unconfigured")
        return None


def save_credentials(creds: Credentials) -> None:
    write_private(_auth_path(), json.dumps({
        "password_hash": creds.password_hash,
        "username": creds.username,
        "session_epoch": creds.session_epoch,
    }, separators=(",", ":")).encode())

    # Left over from the generated-password design: if an install from an older
    # build still has that file, it is a live credential sitting on the config
    # volume and is removed now that a real account exists.
    _remove_quietly(get_settings().config_dir / INITIAL_PASSWORD_FILE)


def set_password(new_password: str) -> Credentials:
    """Store a new password and invalidate every existing session."""
    previous = load_credentials()
    creds = Credentials(
        username=previous.username if previous else "admin",
        password_hash=hash_password(new_password),
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


MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 32
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")


def is_configured() -> bool:
    """True once the user has created their account."""
    return load_credentials() is not None


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not _USERNAME_RE.match(name):
        raise AuthError(
            f"the username must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} "
            "characters, start with a letter or digit, and contain only "
            "letters, digits, dot, dash or underscore")
    return name


def create_account(username: str, password: str) -> None:
    """First-run setup. Refuses once an account exists.

    There is deliberately no generated password and no initial-password file.
    The user chooses both the username and the password the first time they
    open Cadenza, and nothing is ever written to disk that they did not choose.
    """
    if load_credentials() is not None:
        raise AuthError("an account already exists")
    name = validate_username(username)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"the password must be at least {MIN_PASSWORD_LENGTH} characters")
    save_credentials(Credentials(username=name,
                                 password_hash=hash_password(password),
                                 session_epoch=0))
