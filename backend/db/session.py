"""Database engines: async for the API, sync for the RQ worker, Alembic and scripts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings


def _sqlite_fk(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@lru_cache
def async_engine() -> AsyncEngine:
    url = get_settings().DATABASE_URL
    kw = {} if url.startswith("sqlite") else {"pool_size": 10, "max_overflow": 10, "pool_pre_ping": True}
    eng = create_async_engine(url, **kw)
    if url.startswith("sqlite"):
        event.listen(eng.sync_engine, "connect", _sqlite_fk)
    return eng


@lru_cache
def async_sessionmaker_() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker_()() as s:
        yield s


@lru_cache
def sync_engine() -> Engine:
    url = get_settings().sync_database_url
    eng = create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        event.listen(eng, "connect", _sqlite_fk)
    return eng


@contextmanager
def sync_session() -> Iterator[Session]:
    with sessionmaker(sync_engine(), expire_on_commit=False)() as s:
        yield s
