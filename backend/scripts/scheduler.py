"""Tiny scheduler process: daily retention (02:00 UTC) and weekly drift report (Monday 03:00 UTC).

Runs as its own container; a Redis key per run makes it safe to run more than one replica.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from ..core.config import get_settings
from ..core.logging import setup_logging
from ..core.redis import get_redis
from ..services.maintenance import run_retention, store_drift_report

log = logging.getLogger("scheduler")


def _once(key: str, ttl_s: int) -> bool:
    return bool(get_redis().set(f"sched:{key}", "1", nx=True, ex=ttl_s))


def tick(now: datetime) -> None:
    if now.hour >= 2 and _once(f"retention:{now:%Y-%m-%d}", 2 * 86400):
        run_retention(now)
    if now.weekday() == 0 and now.hour >= 3 and _once(f"drift:{now:%G-W%V}", 8 * 86400):
        log.info("drift report stored", extra={"key": store_drift_report(now)})


def main() -> None:
    setup_logging(get_settings().LOG_LEVEL)
    log.info("scheduler started")
    while True:
        try:
            tick(datetime.now(UTC))
        except Exception:
            log.exception("scheduled task failed")
        time.sleep(300)


if __name__ == "__main__":
    main()
