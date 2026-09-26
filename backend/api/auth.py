"""Authentication: httpOnly cookie JWTs (15 min access + 7 day rotating refresh), CSRF double
submit token, lockout, rate limiting, optional TOTP, server-side sessions (revocable)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import update

from ..core import ratelimit
from ..core.config import get_settings
from ..core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    create_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    needs_rehash,
    new_csrf_token,
    new_totp_secret,
    password_problems,
    totp_uri,
    verify_password,
    verify_totp,
)
from ..db.models import Session, User
from ..schemas import LoginIn, PasswordChangeIn, SessionOut, TotpCodeIn, TotpSetupOut, UserOut
from ..services.audit import Actions, record
from .deps import DB, CurrentUser, Meta, aware, current_session, request_meta, user_by_email

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_PATH = "/api/v1/auth"


def _cookie(resp: Response, name: str, value: str, max_age: int, httponly: bool, path: str) -> None:
    s = get_settings()
    resp.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=httponly,
        path=path,
        secure=s.COOKIE_SECURE,
        samesite="strict",
        domain=s.COOKIE_DOMAIN,
    )


def _set_cookies(resp: Response, access: str, refresh: str | None, csrf: str) -> None:
    s = get_settings()
    _cookie(resp, ACCESS_COOKIE, access, s.ACCESS_TOKEN_MINUTES * 60, True, "/")
    if refresh is not None:
        _cookie(resp, REFRESH_COOKIE, refresh, s.REFRESH_TOKEN_DAYS * 86400, True, REFRESH_PATH)
    _cookie(resp, CSRF_COOKIE, csrf, s.REFRESH_TOKEN_DAYS * 86400, False, "/")


def _clear_cookies(resp: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH), (CSRF_COOKIE, "/")):
        resp.delete_cookie(
            name, path=path, domain=s.COOKIE_DOMAIN, secure=s.COOKIE_SECURE, httponly=True, samesite="strict"
        )


def _session_out(user: User, csrf: str) -> SessionOut:
    s = get_settings()
    return SessionOut(
        user=UserOut.model_validate(user),
        csrf_token=csrf,
        access_expires_in=s.ACCESS_TOKEN_MINUTES * 60,
        idle_timeout_seconds=s.IDLE_TIMEOUT_MINUTES * 60,
    )


async def _issue(db: DB, resp: Response, user: User, meta: Meta) -> SessionOut:
    s = get_settings()
    now = datetime.now(UTC)
    sess = Session(
        user_id=user.id,
        refresh_jti="",
        expires_at=now + timedelta(days=s.REFRESH_TOKEN_DAYS),
        ip=meta.ip,
        user_agent=(meta.user_agent or "")[:255],
    )
    db.add(sess)
    await db.flush()
    access, _ = create_token("access", user.id, sess.id, s.ACCESS_TOKEN_MINUTES, role=user.role.value)
    refresh, jti = create_token("refresh", user.id, sess.id, s.REFRESH_TOKEN_DAYS * 1440)
    sess.refresh_jti = jti
    csrf = new_csrf_token()
    _set_cookies(resp, access, refresh, csrf)
    return _session_out(user, csrf)


@router.post("/login", response_model=SessionOut, summary="Sign in (sets httpOnly cookies)")
async def login(body: LoginIn, response: Response, db: DB, meta: Annotated[Meta, Depends(request_meta)]) -> SessionOut:
    s = get_settings()
    ratelimit.hit(f"login:{meta.ip}", s.LOGIN_RATE_PER_MIN, 60)
    user = await user_by_email(db, body.email)
    now = datetime.now(UTC)
    if user and user.locked_until and aware(user.locked_until) > now:
        minutes = max(1, int((aware(user.locked_until) - now).total_seconds() // 60) + 1)
        await record(
            db,
            Actions.LOGIN_FAILED,
            "user",
            user.id,
            user=user,
            ip=meta.ip,
            user_agent=meta.user_agent,
            details={"reason": "locked"},
        )
        raise HTTPException(
            status.HTTP_423_LOCKED, f"Account locked after too many failed sign-ins. Try again in {minutes} minute(s)."
        )
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        if user is not None and ok is False:
            user.failed_logins += 1
            if user.failed_logins >= s.LOCKOUT_THRESHOLD:
                user.locked_until, user.failed_logins = now + timedelta(minutes=s.LOCKOUT_MINUTES), 0
        await record(
            db,
            Actions.LOGIN_FAILED,
            "user",
            user.id if user else None,
            ip=meta.ip,
            user_agent=meta.user_agent,
            details={"reason": "inactive" if ok else "bad_credentials"},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if user.totp_enabled:
        if not body.otp:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                {"code": "otp_required", "message": "Enter the code from your authenticator app."},
            )
        if not verify_totp(decrypt_secret(user.totp_secret_enc or ""), body.otp):
            user.failed_logins += 1
            await record(
                db,
                Actions.LOGIN_FAILED,
                "user",
                user.id,
                user=user,
                ip=meta.ip,
                user_agent=meta.user_agent,
                details={"reason": "bad_otp"},
            )
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, {"code": "otp_invalid", "message": "The authenticator code is not valid."}
            )
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    out = await _issue(db, response, user, meta)
    await record(db, Actions.LOGIN, "user", user.id, user=user, ip=meta.ip, user_agent=meta.user_agent)
    return out


@router.post("/refresh", response_model=SessionOut, summary="Rotate the refresh token (silent refresh)")
async def refresh(
    request: Request, response: Response, db: DB, meta: Annotated[Meta, Depends(request_meta)]
) -> SessionOut:
    s = get_settings()
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token.")
    try:
        claims = decode_token(token, "refresh")
    except jwt.PyJWTError:
        _clear_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.") from None
    sess = await db.get(Session, claims["sid"])
    user = await db.get(User, claims["sub"])
    now = datetime.now(UTC)
    if sess is None or user is None or sess.revoked_at is not None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.")
    if claims["jti"] != sess.refresh_jti:  # an old refresh token was replayed -> assume theft
        sess.revoked_at, sess.revoke_reason = now, "refresh_reuse"
        await record(db, Actions.TOKEN_REUSE, "session", sess.id, user=user, ip=meta.ip, user_agent=meta.user_agent)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.")
    if now - aware(sess.last_seen_at) > timedelta(minutes=s.IDLE_TIMEOUT_MINUTES):
        sess.revoked_at, sess.revoke_reason = now, "idle"
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signed out after 15 minutes of inactivity.")
    access, _ = create_token("access", user.id, sess.id, s.ACCESS_TOKEN_MINUTES, role=user.role.value)
    new_refresh, jti = create_token("refresh", user.id, sess.id, s.REFRESH_TOKEN_DAYS * 1440)
    sess.refresh_jti, sess.last_seen_at = jti, now
    csrf = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
    _set_cookies(response, access, new_refresh, csrf)
    await db.commit()
    return _session_out(user, csrf)


@router.post("/logout", status_code=204, summary="Sign out this session")
async def logout(request: Request, response: Response, db: DB, meta: Annotated[Meta, Depends(request_meta)]) -> None:
    token = request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
    _clear_cookies(response)
    if not token:
        return
    try:
        claims = jwt.decode(
            token, get_settings().SECRET_KEY.get_secret_value(), algorithms=["HS256"], options={"verify_exp": False}
        )
    except jwt.PyJWTError:
        return
    sess = await db.get(Session, claims.get("sid"))
    if sess is not None and sess.revoked_at is None:
        sess.revoked_at, sess.revoke_reason = datetime.now(UTC), "logout"
        user = await db.get(User, sess.user_id)
        await record(db, Actions.LOGOUT, "session", sess.id, user=user, ip=meta.ip, user_agent=meta.user_agent)


@router.post("/logout-all", status_code=204, summary="Sign out every session of this user")
async def logout_all(
    response: Response, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]
) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason="logout_all")
    )
    _clear_cookies(response)
    await record(db, Actions.LOGOUT_ALL, "user", user.id, user=user, ip=meta.ip, user_agent=meta.user_agent)


@router.get("/me", response_model=SessionOut, summary="Current user and CSRF token")
async def me(
    request: Request, response: Response, ctx: Annotated[tuple[User, Session], Depends(current_session)]
) -> SessionOut:
    csrf = request.cookies.get(CSRF_COOKIE)
    if not csrf:
        s = get_settings()
        csrf = new_csrf_token()
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            max_age=s.REFRESH_TOKEN_DAYS * 86400,
            httponly=False,
            path="/",
            secure=s.COOKIE_SECURE,
            samesite="strict",
            domain=s.COOKIE_DOMAIN,
        )
    return _session_out(ctx[0], csrf)


@router.post("/password", status_code=204, summary="Change your password (signs out other sessions)")
async def change_password(
    body: PasswordChangeIn,
    db: DB,
    ctx: Annotated[tuple[User, Session], Depends(current_session)],
    meta: Annotated[Meta, Depends(request_meta)],
) -> None:
    user, sess = ctx
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if problems := password_problems(body.new_password, user.email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Password needs: " + "; ".join(problems))
    user.password_hash, user.must_change_password = hash_password(body.new_password), False
    user.password_changed_at = datetime.now(UTC)
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.id != sess.id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason="password_change")
    )
    await record(db, Actions.PASSWORD_CHANGE, "user", user.id, user=user, ip=meta.ip, user_agent=meta.user_agent)


@router.post("/totp/setup", response_model=TotpSetupOut, summary="Start two-factor setup")
async def totp_setup(db: DB, user: CurrentUser) -> TotpSetupOut:
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor authentication is already enabled.")
    secret = new_totp_secret()
    user.totp_secret_enc = encrypt_secret(secret)
    await db.commit()
    uri = totp_uri(secret, user.email)
    return TotpSetupOut(otpauth_uri=uri, secret=secret, qr_svg=segno.make(uri).svg_inline(scale=4))


@router.post("/totp/enable", status_code=204, summary="Confirm two-factor setup with a code")
async def totp_enable(
    body: TotpCodeIn, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]
) -> None:
    if not user.totp_secret_enc or not verify_totp(decrypt_secret(user.totp_secret_enc), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code is not valid - check your device clock.")
    user.totp_enabled = True
    await record(db, Actions.TOTP_ENABLE, "user", user.id, user=user, ip=meta.ip, user_agent=meta.user_agent)


@router.post("/totp/disable", status_code=204, summary="Turn off two-factor authentication")
async def totp_disable(
    body: TotpCodeIn, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]
) -> None:
    if not user.totp_enabled or not verify_totp(decrypt_secret(user.totp_secret_enc or ""), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code is not valid.")
    user.totp_enabled, user.totp_secret_enc = False, None
    await record(db, Actions.TOTP_DISABLE, "user", user.id, user=user, ip=meta.ip, user_agent=meta.user_agent)
