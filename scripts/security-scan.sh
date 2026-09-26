#!/usr/bin/env bash
# Dependency, secret and image scanning. Exit code != 0 when a HIGH/CRITICAL finding remains.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT=docs/security-scan
mkdir -p "$OUT"
status=0

echo "== pip-audit (Python dependencies of the API + worker images)"
docker run --rm -v "$PWD:/src" -w /src gleasonai-worker-test sh -c \
  "pip install -q pip-audit >/dev/null 2>&1; pip-audit -r backend/requirements.txt -r /tmp/infer-reqs.txt --desc on" \
  | tee "$OUT/pip-audit.txt" || status=1

echo "== npm audit (dashboard, production dependencies)"
(cd frontend && npm audit --omit=dev --audit-level=high) | tee "$OUT/npm-audit.txt" || status=1

echo "== gitleaks (secrets in the working tree)"
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.28.0 dir /repo --no-banner --redact \
  --config /repo/.gitleaks.toml | tee "$OUT/gitleaks.txt" || status=1

echo "== trivy (HIGH/CRITICAL vulnerabilities in the images)"
for img in gleasonai/api gleasonai/worker gleasonai/frontend gleasonai/backup; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache aquasec/trivy:0.66.0 \
    image --quiet --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /dev/null "$img:latest" \
    | tee "$OUT/trivy-$(basename "$img").txt" || status=1
done
exit $status
