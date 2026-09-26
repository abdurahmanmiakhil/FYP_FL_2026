#!/usr/bin/env bash
# One-command first-time setup on a new machine:  make setup   (or ./scripts/setup.sh)
#   1. checks Docker, memory and disk   2. finds/builds the model bundle   3. creates .env
#   4. downloads Phikon + verifies models   5. starts the stack   6. waits until the AI is ready
set -euo pipefail
cd "$(dirname "$0")/.."
ok()   { printf "  \033[32m✔\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✘ %s\033[0m\n" "$*"; exit 1; }

echo "== 1/6 checking this machine"
command -v docker >/dev/null || fail "Docker is not installed (https://docs.docker.com/get-docker/)"
docker info >/dev/null 2>&1 || fail "Docker is installed but not running - start Docker Desktop and retry"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin missing - update Docker"
command -v openssl >/dev/null || fail "openssl is missing"
mem_gb=$(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 / 1024 ))
[ "$mem_gb" -ge 7 ] && ok "Docker memory: ${mem_gb} GB" || warn "Docker has only ${mem_gb} GB RAM - give it at least 8 GB (Docker Desktop -> Settings -> Resources)"
free_gb=$(df -Pk . | awk 'NR==2 {print int($4/1024/1024)}')
[ "$free_gb" -ge 20 ] && ok "free disk: ${free_gb} GB" || warn "only ${free_gb} GB free disk - at least 20 GB recommended (a full disk breaks Docker)"

echo "== 2/6 model bundle (the private thesis models are never in git)"
if [ -f models/bundle/manifest.json ]; then
  ok "models/bundle found"
elif [ -n "${THESIS_DIR:-}" ] && [ -d "${THESIS_DIR}/fl_outputs_phikon" ]; then
  make bundle THESIS_DIR="$THESIS_DIR" && ok "bundle built from $THESIS_DIR"
elif grep -q '^MODEL_SOURCE=hf' .env 2>/dev/null; then
  ok "MODEL_SOURCE=hf - models will be downloaded from Hugging Face"
else
  fail "no models/bundle. Copy it from your old machine (see docs/SETUP_NEW_MACHINE.md step 4) or run:
       make setup THESIS_DIR=\"/path/to/submission_files\""
fi

echo "== 3/6 configuration"
./scripts/make-env.sh
mkdir -p storage/.empty backups secrets && [ -f secrets/hf_token ] || : > secrets/hf_token

echo "== 4/6 verifying models and downloading Phikon (first time ~330 MB)"
make fetch

echo "== 5/6 building and starting (first time 10-20 minutes)"
make up

echo "== 6/6 waiting until the AI worker has loaded the models"
for i in $(seq 1 90); do
  if curl -sk https://localhost/api/v1/ready | grep -q '"models_loaded":true'; then
    ok "GleasonAI is ready at https://localhost"
    echo
    echo "Next:"
    echo "  make seed-admin EMAIL=you@example.com HOSPITAL=\"My Hospital\"   # your administrator"
    echo "  make seed-demo                                                 # demo accounts + 6 demo cases"
    echo "  make tunnel                                                    # public link for anyone online"
    exit 0
  fi
  sleep 10
done
fail "not ready after 15 minutes - check: docker compose -f docker-compose.yml logs worker api"
