#!/bin/bash
# Encrypted backup: PostgreSQL (pg_dump custom format) + the S3 bucket (tar). Files in /backups:
#   db-<stamp>.dump.enc, storage-<stamp>.tar.gz.enc, <stamp>.sha256, LATEST
set -euo pipefail
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
export RCLONE_CONFIG_S3_ACCESS_KEY_ID="$S3_ADMIN_ACCESS_KEY" RCLONE_CONFIG_S3_SECRET_ACCESS_KEY="$S3_ADMIN_SECRET"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out=/backups
enc() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE; }

echo "[$stamp] dumping database"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h "${POSTGRES_HOST:-db}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  | enc > "$out/db-$stamp.dump.enc.part"
mv "$out/db-$stamp.dump.enc.part" "$out/db-$stamp.dump.enc"

echo "[$stamp] copying object storage"
tmp=$(mktemp -d)
rclone copy --quiet "s3:${S3_BUCKET:-gleasonai}" "$tmp/bucket"
mkdir -p "$tmp/bucket"
tar -C "$tmp" -czf - bucket | enc > "$out/storage-$stamp.tar.gz.enc.part"
mv "$out/storage-$stamp.tar.gz.enc.part" "$out/storage-$stamp.tar.gz.enc"
rm -rf "$tmp"

(cd "$out" && sha256sum "db-$stamp.dump.enc" "storage-$stamp.tar.gz.enc" > "$stamp.sha256")
find "$out" -type f \( -name 'db-*' -o -name 'storage-*' -o -name '*.sha256' \) -mtime "+${BACKUP_KEEP_DAYS:-14}" -delete
echo "$stamp" > "$out/LATEST"
echo "[$stamp] backup complete: $(du -ch "$out"/*"$stamp"* | tail -1 | cut -f1)"
