"""Liveness (/health), readiness (/ready: models loaded?) and Prometheus metrics (/metrics)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from ..core.redis import get_redis
from ..schemas import Health
from ..services.jobs import queue_length
from ..services.model_info import worker_status
from ..services.storage import get_storage
from .deps import DB

router = APIRouter(tags=["health"])


async def _health(db: DB) -> Health:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    def sync_checks() -> tuple[bool, bool, int, int]:
        try:
            redis_ok = bool(get_redis().ping())
            ql = queue_length()
        except Exception:
            redis_ok, ql = False, -1
        try:
            storage_ok = get_storage().ping()
        except Exception:
            storage_ok = False
        return redis_ok, storage_ok, len(worker_status()), ql

    redis_ok, storage_ok, workers, ql = await run_in_threadpool(sync_checks)
    ok = db_ok and redis_ok and storage_ok
    return Health(
        status="ok" if ok else "degraded",
        database=db_ok,
        redis=redis_ok,
        storage=storage_ok,
        workers=workers,
        models_loaded=workers > 0,
        queue_length=ql,
    )


@router.get("/health", response_model=Health, summary="Liveness and dependency status")
async def health(db: DB, response: Response) -> Health:
    h = await _health(db)
    if h.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return h


@router.get("/ready", response_model=Health, summary="Ready to predict (a worker has the models loaded)")
async def ready(db: DB, response: Response) -> Health:
    h = await _health(db)
    if h.status != "ok" or not h.models_loaded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return h
