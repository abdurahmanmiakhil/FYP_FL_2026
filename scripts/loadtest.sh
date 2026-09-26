#!/usr/bin/env bash
# Locust load test against the running stack: 20 viewers + 5 parallel uploads for 4 minutes.
# Needs `pip install locust` and the demo accounts (make seed-e2e). Reports go to docs/loadtest/.
set -euo pipefail
cd "$(dirname "$0")/.."
export LOAD_SLIDES_DIR=${LOAD_SLIDES_DIR:-/tmp/gleasonai-load}
mkdir -p "$LOAD_SLIDES_DIR" docs/loadtest
for i in 1 2 3 4 5; do  # unique slides (identical files would be deduplicated)
  docker run --rm -v "$LOAD_SLIDES_DIR:/out" gleasonai/worker:latest python -m prostate_infer.synthetic \
    "/out/load-$i.tiff" --width 8960 --height 4480 --seed "$((RANDOM + i))" >/dev/null
done
locust -f scripts/loadtest/locustfile.py --host "${HOST:-https://localhost}" -u 25 -r 5 -t "${DURATION:-4m}" \
  --headless --only-summary --csv docs/loadtest/results --html docs/loadtest/report.html
