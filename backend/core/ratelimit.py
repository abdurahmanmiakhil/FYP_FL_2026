"""Fixed-window rate limiting in Redis (shared across API replicas)."""

from __future__ import annotations

import time

from fastapi import HTTPException, status

from .redis import get_redis


def hit(key: str, limit: int, window_s: int) -> None:
    """Count one event for `key`; raise 429 when more than `limit` happen in the window."""
    bucket = int(time.time() // window_s)
    k = f"rl:{key}:{bucket}"
    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(k)
    pipe.expire(k, window_s + 1)
    count = int(pipe.execute()[0])
    if count > limit:
        retry = window_s - int(time.time() % window_s)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests - please wait and try again.",
            headers={"Retry-After": str(retry)},
        )
