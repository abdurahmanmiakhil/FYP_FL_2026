"""Worker entry point: load and verify the models once, then serve RQ jobs in-process.

SimpleWorker (no fork per job) keeps Phikon + the 5 heads in memory and is CUDA-safe.
"""

from __future__ import annotations

import logging
import socket

from prometheus_client import start_http_server
from rq import SimpleWorker

from ..core.config import get_settings
from ..core.logging import setup_logging
from ..core.metrics import MODEL_LOADED, WORKER_REGISTRY
from ..core.redis import get_redis
from ..services.jobs import QUEUE
from ..services.model_info import publish_worker, unpublish_worker

log = logging.getLogger("worker")


def worker_name() -> str:
    return f"worker-{socket.gethostname()}"


def main() -> None:
    s = get_settings()
    setup_logging(s.LOG_LEVEL)
    from prostate_infer.predict import get_engine

    engine = get_engine("auto")  # verifies sha256 of every model file; refuses to start on mismatch
    a = engine.assets
    thresholds = {**a.operating_points, **{f"isup_{k + 1}": t for k, t in enumerate(a.ensemble_thresholds)}}
    name = worker_name()
    publish_worker(name, a.version, str(engine.device), thresholds)
    MODEL_LOADED.set(1)
    start_http_server(9101, registry=WORKER_REGISTRY)
    log.info("models loaded", extra={"model_version": a.version[:16], "device": str(engine.device)})
    try:
        SimpleWorker([QUEUE], connection=get_redis(), name=name).work(with_scheduler=True, logging_level=s.LOG_LEVEL)
    finally:
        unpublish_worker(name)


if __name__ == "__main__":
    main()
