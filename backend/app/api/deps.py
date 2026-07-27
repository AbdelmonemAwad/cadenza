"""Shared API dependencies."""
from __future__ import annotations

from fastapi import Cookie, Header, HTTPException, status

from app.core.auth import SUBJECT_PASSWORD_RESET, verify_session

SESSION_COOKIE = "cadenza_session"

_UNAUTHORISED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def _resolve_subject(session: str | None, authorization: str | None) -> str | None:
    """Accept a session from either the cookie or a bearer token.

    Both are tried independently rather than cookie-else-header: a stale cookie
    left in the browser would otherwise suppress a perfectly valid bearer
    token, locking out the CLI whenever an old cookie happened to be present.
    """
    subject = verify_session(session)
    if subject is not None:
        return subject
    if authorization and authorization.strip().lower().startswith("bearer "):
        return verify_session(authorization.strip()[7:].strip())
    return None


def current_user(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> str:
    """Reject anything without a valid, fully privileged session.

    Applied at the router level so a new endpoint is protected by default
    rather than by remembering to decorate it.

    Deliberately takes no `Request` parameter: FastAPI does not resolve one for
    a dependency attached via `APIRouter(dependencies=[...])`.
    """
    subject = _resolve_subject(session, authorization)
    if subject is None:
        raise _UNAUTHORISED
    if subject == SUBJECT_PASSWORD_RESET:
        # Signed in with the generated first-run password. The change is
        # enforced here, server-side; relying on the UI to present the form
        # would let a page refresh skip it permanently.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password change required before using the API",
        )
    return subject


def user_or_password_reset(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> str:
    """Like current_user, but also accepts a first-run password-reset session.

    Used only by the change-password endpoint, which must be reachable while
    the forced change is outstanding.
    """
    subject = _resolve_subject(session, authorization)
    if subject is None:
        raise _UNAUTHORISED
    return subject
