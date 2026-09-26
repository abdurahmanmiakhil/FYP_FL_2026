"""Prometheus metrics. The API serves /metrics (internal network only - the proxy blocks it);
each worker serves its own on :9101."""

from __future__ import annotations

from collections.abc import Iterator

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

API_REGISTRY = CollectorRegistry()
WORKER_REGISTRY = CollectorRegistry()

HTTP_LATENCY = Histogram(
    "gleasonai_http_request_duration_seconds",
    "API request latency",
    ["method", "route", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10),
    registry=API_REGISTRY,
)
HTTP_ERRORS = Counter("gleasonai_http_errors_total", "5xx responses", ["route"], registry=API_REGISTRY)

JOB_DURATION = Histogram(
    "gleasonai_job_duration_seconds",
    "Time to process one slide",
    ["outcome"],
    buckets=(5, 10, 20, 30, 60, 90, 120, 180, 300, 600, 1200),
    registry=WORKER_REGISTRY,
)
JOBS = Counter("gleasonai_jobs_total", "Prediction jobs by outcome", ["outcome"], registry=WORKER_REGISTRY)
GPU_MEMORY = Gauge("gleasonai_gpu_memory_bytes", "GPU memory allocated by the worker", registry=WORKER_REGISTRY)
MODEL_LOADED = Gauge("gleasonai_worker_models_loaded", "1 when models are loaded", registry=WORKER_REGISTRY)


class QueueCollector(Collector):
    """Scrape-time gauges read from Redis (queue length, ready workers, failed jobs)."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        from ..services.jobs import queue
        from ..services.model_info import worker_status

        try:
            q = queue()
            ql, failed = len(q), q.failed_job_registry.count
            workers = len(worker_status())
        except Exception:
            ql, failed, workers = -1, -1, 0
        yield GaugeMetricFamily("gleasonai_queue_length", "Jobs waiting in the prediction queue", value=ql)
        yield GaugeMetricFamily("gleasonai_failed_jobs", "Jobs in the RQ failed registry", value=failed)
        yield GaugeMetricFamily("gleasonai_workers_ready", "Workers with models loaded", value=workers)


API_REGISTRY.register(QueueCollector())
