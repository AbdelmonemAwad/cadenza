"""Sign-in, sign-out and password management.

This router is mounted WITHOUT the authentication dependency; every other
router requires it.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import SESSION_COOKIE, user_or_password_reset
from app.core.auth import (
    MIN_PASSWORD_LENGTH,
    SUBJECT_ADMIN,
    SUBJECT_PASSWORD_RESET,
    issue_session,
    load_credentials,
    revoke_all_sessions,
    set_password,
    verify_password,
)
from app.core.ratelimit import client_key, login_limiter

log = logging.getLogger(__name__)

router = APIRouter()

SESSION_MAX_AGE = 60 * 60 * 24 * 14


class LoginRequest(BaseModel):
    password: str = Field(max_length=1024)     # bound the work an attacker can request


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,          # unreachable from JavaScript, so XSS cannot lift it
        samesite="strict",      # no cross-site request carries it
        secure=False,           # DSM is commonly plain http on the LAN
        path="/",
        max_age=SESSION_MAX_AGE,
    )


@router.get("/status")
async def status_endpoint() -> dict:
    """Whether the instance has credentials yet. Safe to call unauthenticated.

    Deliberately does not reveal whether the generated password is still in
    force: that would tell an unauthenticated caller the box is still on its
    factory credential. The UI learns it from the login response instead.
    """
    return {"configured": load_credentials() is not None}


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    creds = load_credentials()
    if creds is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "authentication is not initialised yet")

    # Checked before the hash is computed, so a throttled caller cannot keep
    # the thread pool busy either.
    key = client_key(request)
    retry_after = login_limiter.check(key)
    if retry_after > 0:
        log.warning("sign-in throttled for %s", key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many sign-in attempts",
            headers={"Retry-After": str(int(retry_after) + 1)})

    # scrypt is deliberately expensive (~100 ms). Running it inline would block
    # the event loop for that long, so an unauthenticated caller could stall
    # the whole server just by hammering this endpoint.
    ok = await asyncio.to_thread(verify_password, body.password, creds.password_hash)
    if not ok:
        login_limiter.record_failure(key)
        log.warning("failed sign-in attempt from %s", key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    login_limiter.record_success(key)

    # While the generated password is still in force the session is scoped to
    # the password endpoints only; every other route rejects it.
    subject = SUBJECT_PASSWORD_RESET if creds.must_change else SUBJECT_ADMIN
    _set_session_cookie(response, issue_session(subject))
    return {"authenticated": True, "must_change_password": creds.must_change}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.post("/logout-everywhere")
async def logout_everywhere(response: Response,
                            _user: str = Depends(user_or_password_reset)) -> dict:
    """Invalidate every issued token, not just this browser's cookie.

    Plain sign-out only clears the local cookie; the signed token stays valid
    until it expires. This is the control for a token believed to be leaked.
    """
    revoke_all_sessions()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False, "revoked": True}


@router.post("/password")
async def change_password(body: ChangePasswordRequest, request: Request,
                          response: Response,
                          _user: str = Depends(user_or_password_reset)) -> dict:
    creds = load_credentials()
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    # Throttled as well as login: this endpoint also verifies the current
    # password, so leaving it open would just move the guessing here. A session
    # is required, but a stolen cookie is exactly the case that matters.
    key = client_key(request)
    retry_after = login_limiter.check(key)
    if retry_after > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts",
            headers={"Retry-After": str(int(retry_after) + 1)})

    ok = await asyncio.to_thread(verify_password, body.current_password, creds.password_hash)
    if not ok:
        login_limiter.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    login_limiter.record_success(key)
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "the new password must differ from the current one")

    # Bumps the session epoch, which is what actually ends every other session.
    await asyncio.to_thread(set_password, body.new_password)
    # Re-issue for this browser so the change does not sign the user out of the
    # tab they are using.
    _set_session_cookie(response, issue_session(SUBJECT_ADMIN))
    return {"changed": True}
