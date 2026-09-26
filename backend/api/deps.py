"""Shared API dependencies: current user (cookie JWT + server-side session), roles, row access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.security import ACCESS_COOKIE, decode_token
from ..db.models import Case, Role, Session, User
from ..db.session import get_db

DB = Annotated[AsyncSession, Depends(get_db)]

UNAUTH = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "Not signed in or session expired.", headers={"WWW-Authenticate": "Cookie"}
)


@dataclass
class Meta:
    ip: str | None
    user_agent: str | None


def request_meta(request: Request) -> Meta:
    return Meta(request.client.host if request.client else None, request.headers.get("user-agent"))


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def current_session(request: Request, db: DB) -> tuple[User, Session]:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UNAUTH
    try:
        claims = decode_token(token, "access")
    except jwt.PyJWTError:
        raise UNAUTH from None
    sess = await db.get(Session, claims["sid"])
    now = datetime.now(UTC)
    s = get_settings()
    if sess is None or sess.revoked_at is not None or aware(sess.expires_at) < now:
        raise UNAUTH
    if now - aware(sess.last_seen_at) > timedelta(minutes=s.IDLE_TIMEOUT_MINUTES):
        sess.revoked_at, sess.revoke_reason = now, "idle"
        await db.commit()
        raise UNAUTH
    user = await db.get(User, claims["sub"])
    if user is None or not user.is_active or sess.user_id != user.id:
        raise UNAUTH
    if now - aware(sess.last_seen_at) > timedelta(seconds=30):  # sliding idle window, throttled writes
        sess.last_seen_at = now
        await db.commit()
    request.state.user_id = user.id
    return user, sess


async def current_user(ctx: Annotated[tuple[User, Session], Depends(current_session)]) -> User:
    return ctx[0]


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: Role):  # type: ignore[no-untyped-def]
    async def dep(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this.")
        return user

    return Depends(dep)


AdminUser = Annotated[User, require_roles(Role.admin)]
Clinician = Annotated[User, require_roles(Role.pathologist, Role.urologist)]


def visible_cases(user: User):  # type: ignore[no-untyped-def]
    """Row-level access: own hospital only, unless admin; soft-deleted cases are hidden."""
    q = select(Case).where(Case.deleted_at.is_(None))
    if user.role != Role.admin:
        q = q.where(Case.hospital == user.hospital)
    return q


async def get_case_for(db: AsyncSession, user: User, case_id: str) -> Case:
    case = (await db.execute(visible_cases(user).where(Case.id == case_id))).unique().scalar_one_or_none()
    if case is None:  # 404 (not 403) so other hospitals' case ids are not disclosed
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return case


async def user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
