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
    AuthError,
    create_account,
    is_configured,
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


class SetupRequest(BaseModel):
    """First-run account creation. Both values are the user's choice."""
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=32)
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
    """Whether an account exists yet. Safe to call unauthenticated.

    The UI uses this to decide between the setup form and the sign-in form.
    It reveals only that a fresh install has not been claimed, which the setup
    page makes obvious anyway.
    """
    return {"configured": is_configured()}


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(body: SetupRequest, request: Request, response: Response) -> dict:
    """Create the account, once, on a fresh install.

    Cadenza generates nothing. There is no default password, no factory
    credential and no file containing a secret the user did not choose -- the
    first person to open the interface picks the username and the password.

    Rate limited like sign-in: this endpoint is reachable without a session
    until it has been used, so it must not be a free way to hammer the box.
    """
    key = client_key(request)
    decision = login_limiter.reserve(key)
    if decision.blocked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts",
            headers={"Retry-After": str(int(decision.retry_after) + 1)})

    try:
        await asyncio.to_thread(create_account, body.username, body.password)
    except AuthError as exc:
        # Already configured is a conflict, not a validation error: it means
        # somebody else claimed this install first.
        code = (status.HTTP_409_CONFLICT if "already exists" in str(exc)
                else status.HTTP_400_BAD_REQUEST)
        raise HTTPException(code, str(exc)) from None

    login_limiter.record_success(key)
    log.warning("account created for %s", body.username)
    _set_session_cookie(response, issue_session(SUBJECT_ADMIN))
    return {"created": True, "username": body.username}


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    creds = load_credentials()
    if creds is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "no account yet: create one at /api/v1/auth/setup")

    # Reserved before the hash is computed. This both keeps a throttled caller
    # from occupying the thread pool and closes the window that made the
    # throttle nearly useless: check and record used to be separate calls with
    # ~100 ms of scrypt between them, so a concurrent burst all passed the
    # check before any of them had been counted.
    key = client_key(request)
    decision = login_limiter.reserve(key)
    if decision.blocked:
        log.warning("sign-in throttled for %s", key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many sign-in attempts",
            headers={"Retry-After": str(int(decision.retry_after) + 1)})
    if decision.delay:
        # Global pressure: everyone waits a little. Deliberately a delay and
        # not a refusal, so a burst of failures cannot lock the owner out.
        await asyncio.sleep(decision.delay)

    # scrypt is deliberately expensive (~100 ms). Running it inline would block
    # the event loop for that long, so an unauthenticated caller could stall
    # the whole server just by hammering this endpoint.
    # The password is checked even when the username is wrong, so the response
    # time does not reveal which of the two was incorrect.
    ok = await asyncio.to_thread(verify_password, body.password, creds.password_hash)
    if body.username and body.username.strip().lower() != creds.username.lower():
        ok = False
    if not ok:
        # The attempt is already counted; nothing to add here.
        log.warning("failed sign-in attempt from %s", key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    login_limiter.record_success(key)

    _set_session_cookie(response, issue_session(SUBJECT_ADMIN))
    return {"authenticated": True, "username": creds.username}


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
    decision = login_limiter.reserve(key)
    if decision.blocked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts",
            headers={"Retry-After": str(int(decision.retry_after) + 1)})
    if decision.delay:
        await asyncio.sleep(decision.delay)

    ok = await asyncio.to_thread(verify_password, body.current_password, creds.password_hash)
    if not ok:
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
