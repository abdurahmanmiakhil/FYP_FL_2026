#!/bin/bash
# Restore an encrypted backup:  restore.sh <stamp|latest>
# Verifies checksums, restores the database (replacing existing objects) and the S3 bucket.
set -euo pipefail
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
export RCLONE_CONFIG_S3_ACCESS_KEY_ID="$S3_ADMIN_ACCESS_KEY" RCLONE_CONFIG_S3_SECRET_ACCESS_KEY="$S3_ADMIN_SECRET"
stamp=${1:-latest}
[ "$stamp" = latest ] && stamp=$(cat /backups/LATEST)
cd /backups
sha256sum -c "$stamp.sha256"
dec() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE; }

echo "restoring database from $stamp"
dec < "db-$stamp.dump.enc" | PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h "${POSTGRES_HOST:-db}" -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction

echo "restoring object storage from $stamp"
tmp=$(mktemp -d)
dec < "storage-$stamp.tar.gz.enc" | tar -C "$tmp" -xzf -
rclone mkdir "s3:${S3_BUCKET:-gleasonai}"
rclone sync --quiet "$tmp/bucket" "s3:${S3_BUCKET:-gleasonai}"
rm -rf "$tmp"
echo "restore of $stamp complete"
