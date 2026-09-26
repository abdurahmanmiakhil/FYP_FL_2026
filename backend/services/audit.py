"""Append-only, hash-chained audit trail.

Each row stores hash = sha256(prev_hash + canonical JSON of the row), so editing or deleting
any row breaks the chain (verify_chain). PostgreSQL additionally blocks UPDATE/DELETE with a
trigger (migration 0001). A PostgreSQL advisory lock serialises writers so the chain is linear.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..core.logging import request_id_var
from ..db.models import AuditLog

GENESIS = "0" * 64
LOCK_ID = 7_301_2026  # advisory lock key for the audit chain


class Actions:
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    TOKEN_REUSE = "token_reuse"
    PASSWORD_CHANGE = "password_change"
    TOTP_ENABLE = "totp_enable"
    TOTP_DISABLE = "totp_disable"
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    PASSWORD_RESET = "password_reset"
    UPLOAD = "upload"
    VIEW = "view"
    PREDICT = "predict"
    PREDICT_FAILED = "predict_failed"
    REVIEW = "review"
    EXPORT = "export"
    DELETE = "delete"
    RETENTION = "retention_delete"


def _row_hash(prev: str, row: dict[str, Any]) -> str:
    canon = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256((prev + canon).encode()).hexdigest()


def _payload(
    at: datetime, user_id: str | None, action: str, entity: str, entity_id: str | None, details: dict[str, Any]
) -> dict[str, Any]:
    return {
        "at": at.astimezone(UTC).isoformat(),
        "user_id": user_id,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "details": details,
    }


def _build(
    prev: str,
    user_id: str | None,
    user_email: str | None,
    action: str,
    entity: str,
    entity_id: str | None,
    ip: str | None,
    user_agent: str | None,
    details: dict[str, Any],
) -> AuditLog:
    at = datetime.now(UTC)
    return AuditLog(
        at=at,
        user_id=user_id,
        user_email=user_email,
        action=action,
        entity=entity,
        entity_id=entity_id,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        request_id=request_id_var.get(),
        details=details,
        prev_hash=prev,
        hash=_row_hash(prev, _payload(at, user_id, action, entity, entity_id, details)),
    )


_LAST_SQL = select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)


async def record(
    db: AsyncSession,
    action: str,
    entity: str,
    entity_id: str | None = None,
    *,
    user: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_ID})
    prev = (await db.execute(_LAST_SQL)).scalar() or GENESIS
    db.add(
        _build(
            prev,
            getattr(user, "id", None),
            getattr(user, "email", None),
            action,
            entity,
            entity_id,
            ip,
            user_agent,
            details or {},
        )
    )
    if commit:
        await db.commit()


def record_sync(
    db: Session,
    action: str,
    entity: str,
    entity_id: str | None = None,
    *,
    user_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Worker-side audit entry (system actor); the caller commits."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_ID})
    prev = db.execute(_LAST_SQL).scalar() or GENESIS
    db.add(
        _build(
            prev, user_id, "system" if user_id is None else None, action, entity, entity_id, None, None, details or {}
        )
    )


async def verify_chain(db: AsyncSession) -> tuple[bool, int | None]:
    """Recompute every hash. Returns (ok, id of the first broken row)."""
    prev = GENESIS
    for row in (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars():
        payload = _payload(
            row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC),
            row.user_id,
            row.action,
            row.entity,
            row.entity_id,
            row.details,
        )
        if row.prev_hash != prev or row.hash != _row_hash(prev, payload):
            return False, row.id
        prev = row.hash
    return True, None
