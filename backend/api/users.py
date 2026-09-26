"""User management (admin only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update

from ..core.security import hash_password, password_problems
from ..db.models import Role, Session, User
from ..schemas import Page, PasswordResetIn, UserCreate, UserOut, UserUpdate
from ..services.audit import Actions, record
from .deps import DB, AdminUser, Meta, request_meta, user_by_email

router = APIRouter(prefix="/users", tags=["users (admin)"])


def _check_password(pw: str, email: str) -> None:
    if problems := password_problems(pw, email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Password needs: " + "; ".join(problems))


async def _revoke_sessions(db: DB, user_id: str, reason: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
    )


@router.get("", response_model=Page[UserOut])
async def list_users(
    db: DB,
    _: AdminUser,
    q: str | None = None,
    role: Role | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[UserOut]:
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(like),
                func.lower(User.full_name).like(like),
                func.lower(User.hospital).like(like),
            )
        )
    if role:
        stmt = stmt.where(User.role == role)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return Page[UserOut](items=[UserOut.model_validate(u) for u in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]) -> User:
    email = body.email.lower()
    if await user_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")
    _check_password(body.password, email)
    user = User(
        email=email,
        full_name=body.full_name.strip(),
        role=body.role,
        hospital=body.hospital.strip(),
        password_hash=hash_password(body.password),
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    await record(
        db,
        Actions.USER_CREATE,
        "user",
        user.id,
        user=admin,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"role": body.role.value, "hospital": user.hospital},
    )
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str, body: UserUpdate, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("is_active") is False or changes.get("role", Role.admin) != Role.admin):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or demote your own account.")
    for k, v in changes.items():
        setattr(user, k, v.strip() if isinstance(v, str) else v)
    if changes.get("is_active") is False or "role" in changes or "hospital" in changes:
        await _revoke_sessions(db, user.id, "account_changed")
    if changes.get("is_active") is True:
        user.failed_logins, user.locked_until = 0, None
    await record(
        db,
        Actions.USER_UPDATE,
        "user",
        user.id,
        user=admin,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()},
    )
    return user


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(
    user_id: str, body: PasswordResetIn, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    _check_password(body.new_password, user.email)
    user.password_hash, user.must_change_password = hash_password(body.new_password), True
    user.failed_logins, user.locked_until, user.password_changed_at = 0, None, datetime.now(UTC)
    await _revoke_sessions(db, user.id, "password_reset")
    await record(db, Actions.PASSWORD_RESET, "user", user.id, user=admin, ip=meta.ip, user_agent=meta.user_agent)
