"""Docker healthcheck for the worker: Redis reachable and this worker registered with models loaded."""

from __future__ import annotations

import sys

from ..core.redis import get_redis
from ..services.model_info import READY_KEY
from .run import worker_name


def main() -> int:
    try:
        return 0 if get_redis().hexists(READY_KEY, worker_name()) else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
