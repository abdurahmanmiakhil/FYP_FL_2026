"""Background jobs (Redis + RQ). The API enqueues by dotted path, so it never imports torch."""

from __future__ import annotations

import json
import secrets
from typing import Any

from rq import Queue, Retry

from ..core.config import get_settings
from ..core.redis import get_redis

QUEUE = "predictions"
TASK = "backend.worker.tasks.process_case"


def queue() -> Queue:
    return Queue(QUEUE, connection=get_redis(), default_timeout=get_settings().JOB_TIMEOUT_SECONDS)


def enqueue_prediction(case_id: str) -> str:
    s = get_settings()
    if s.JOBS_SYNC:  # tests: run inline
        from ..worker.tasks import process_case

        process_case(case_id)
        return f"sync-{case_id}"
    job = queue().enqueue(
        TASK,
        case_id,
        job_id=f"case-{case_id}-{secrets.token_hex(4)}",
        retry=Retry(max=s.JOB_RETRIES, interval=[15, 60]),
        job_timeout=s.JOB_TIMEOUT_SECONDS,
        failure_ttl=7 * 86400,
        result_ttl=86400,
    )
    return str(job.id)


# --- live progress (worker -> Redis -> SSE)


def progress_key(case_id: str) -> str:
    return f"progress:{case_id}"


def publish_progress(case_id: str, **fields: Any) -> None:
    r = get_redis()
    r.set(progress_key(case_id), json.dumps(fields), ex=3600)


def read_progress(case_id: str) -> dict[str, Any] | None:
    raw = get_redis().get(progress_key(case_id))
    return json.loads(raw) if raw else None


def queue_length() -> int:
    return len(queue())
