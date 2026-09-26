#!/usr/bin/env bash
# Create .env from .env.example with strong random secrets (never overwrites an existing .env).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then echo ".env already exists - leaving it unchanged"; exit 0; fi
rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }
cp .env.example .env
set_var() { # portable in-place edit (macOS + Linux)
  local k=$1 v=$2
  awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' .env > .env.tmp && mv .env.tmp .env
}
set_var SECRET_KEY "$(rand 64)"
set_var POSTGRES_PASSWORD "$(rand 32)"
set_var REDIS_PASSWORD "$(rand 32)"
set_var S3_ADMIN_SECRET "$(rand 40)"
set_var BACKUP_PASSPHRASE "$(rand 48)"
set_var GRAFANA_ADMIN_PASSWORD "$(rand 24)"
set_var S3_APP_SECRET "$(rand 40)"
chmod 600 .env
mkdir -p secrets backups storage/.empty
[ -f secrets/hf_token ] || { : > secrets/hf_token; chmod 600 secrets/hf_token; }
echo ".env created with random secrets. Keep a copy of BACKUP_PASSPHRASE somewhere safe (off this server)."
