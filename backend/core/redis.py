"""Redis connection (jobs, progress, rate limits). Tests swap in fakeredis via set_redis()."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis

from .config import get_settings

_override: Redis | None = None


@lru_cache
def _redis() -> Redis:
    return Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=3, socket_timeout=10)


def get_redis() -> Redis:
    return _override if _override is not None else _redis()


def set_redis(r: Redis | None) -> None:
    global _override
    _override = r
