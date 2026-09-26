"""Auth flow: cookies, CSRF, lockout, rate limit, refresh rotation, idle timeout, logout, TOTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from httpx import AsyncClient

from backend.core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, REFRESH_COOKIE
from backend.db.models import Role, Session
from backend.db.session import sync_session

from .conftest import PASSWORD, login, make_user, other_client

API = "/api/v1"


async def test_login_sets_httponly_cookies_and_me(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    r = await client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == u.email and body["user"]["role"] == "pathologist"
    assert "password_hash" not in body["user"]
    cookies = r.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith(ACCESS_COOKIE))
    refresh = next(c for c in cookies if c.startswith(REFRESH_COOKIE))
    csrf = next(c for c in cookies if c.startswith(CSRF_COOKIE))
    assert "HttpOnly" in access and "SameSite=strict" in access
    assert "HttpOnly" in refresh and "Path=/api/v1/auth" in refresh
    assert "HttpOnly" not in csrf
    me = await client.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == u.id


async def test_wrong_password_is_generic_401(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    for email in (u.email, "nobody@test.example"):
        r = await client.post(f"{API}/auth/login", json={"email": email, "password": "wrong-password-1A!"})
        assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password."


async def test_lockout_after_five_failures(client: AsyncClient, redis) -> None:  # type: ignore[no-untyped-def]
    u = make_user(Role.pathologist)
    for _ in range(5):
        await client.post(f"{API}/auth/login", json={"email": u.email, "password": "bad-Password-9"})
    redis.flushall()  # rate limiter reset, so we see the lockout rather than 429
    r = await client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 423 and "locked" in r.json()["detail"]


async def test_login_rate_limited_per_ip(client: AsyncClient) -> None:
    codes = [
        (await client.post(f"{API}/auth/login", json={"email": "x@test.example", "password": "y"})).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429


async def test_inactive_user_cannot_login(client: AsyncClient) -> None:
    u = make_user(Role.pathologist, is_active=False)
    r = await client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 401


async def test_csrf_required_for_state_changes(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    del client.headers[CSRF_HEADER]
    r = await client.post(f"{API}/auth/logout-all")
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    client.headers[CSRF_HEADER] = "wrong"
    assert (await client.post(f"{API}/auth/logout-all")).status_code == 403


async def test_unauthenticated_requests_rejected(client: AsyncClient) -> None:
    for path in ("/auth/me", "/cases", "/stats", "/users", "/slides/x.dzi"):
        assert (await client.get(API + path)).status_code == 401


async def test_refresh_rotates_and_detects_reuse(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    old_refresh = client.cookies[REFRESH_COOKIE]
    r = await client.post(f"{API}/auth/refresh")
    assert r.status_code == 200
    new_refresh = client.cookies[REFRESH_COOKIE]
    assert new_refresh != old_refresh
    # replaying the old refresh token (e.g. stolen) revokes the whole session
    csrf = client.cookies[CSRF_COOKIE]
    async with other_client(**{REFRESH_COOKIE: old_refresh, CSRF_COOKIE: csrf}) as thief:
        thief.headers[CSRF_HEADER] = csrf
        assert (await thief.post(f"{API}/auth/refresh")).status_code == 401
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_logout_revokes_session_server_side(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    access = client.cookies[ACCESS_COOKIE]
    assert (await client.post(f"{API}/auth/logout")).status_code == 204
    async with other_client(**{ACCESS_COOKIE: access}) as thief:
        assert (await thief.get(f"{API}/auth/me")).status_code == 401  # old access token no longer works


async def test_idle_timeout(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    with sync_session() as db:
        for s in db.query(Session).filter(Session.user_id == u.id):
            s.last_seen_at = datetime.now(UTC) - timedelta(minutes=16)
        db.commit()
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_logout_all_revokes_every_session(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    async with other_client() as other:
        await login(other, u)
        assert (await other.post(f"{API}/auth/logout-all")).status_code == 204
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_password_change_policy(client: AsyncClient) -> None:
    u = make_user(Role.pathologist)
    await login(client, u)
    weak = await client.post(f"{API}/auth/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    bad_current = await client.post(
        f"{API}/auth/password", json={"current_password": "nope", "new_password": "Another-Strong-77"}
    )
    assert bad_current.status_code == 400
    ok = await client.post(
        f"{API}/auth/password", json={"current_password": PASSWORD, "new_password": "Another-Strong-77"}
    )
    assert ok.status_code == 204
    await client.post(f"{API}/auth/logout")
    r = await client.post(f"{API}/auth/login", json={"email": u.email, "password": "Another-Strong-77"})
    assert r.status_code == 200


async def test_totp_enrolment_and_login(client: AsyncClient) -> None:
    u = make_user(Role.admin)
    await login(client, u)
    setup = (await client.post(f"{API}/auth/totp/setup")).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in setup["qr_svg"]
    totp = pyotp.TOTP(setup["secret"])
    assert (await client.post(f"{API}/auth/totp/enable", json={"code": "000000"})).status_code == 400
    assert (await client.post(f"{API}/auth/totp/enable", json={"code": totp.now()})).status_code == 204
    await client.post(f"{API}/auth/logout")
    need = await client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    assert need.status_code == 401 and need.json()["detail"]["code"] == "otp_required"
    ok = await client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD, "otp": totp.now()})
    assert ok.status_code == 200 and ok.json()["user"]["totp_enabled"] is True


async def test_security_headers(client: AsyncClient) -> None:
    r = await client.get(f"{API}/health")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Request-ID"]
