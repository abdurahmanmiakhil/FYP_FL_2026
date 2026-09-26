#!/usr/bin/env bash
# Backup/restore drill: take an encrypted backup of the running stack, restore it into a
# throw-away PostgreSQL + SeaweedFS, and compare row counts and object counts with the source.
set -euo pipefail
cd "$(dirname "$0")/.."
C="docker compose -f docker-compose.yml"
set -a; . ./.env; set +a
NET=gleasonai_default

echo "1/4 backup of the live system"
$C run --rm backup backup | tail -2
STAMP=$(cat backups/LATEST)

echo "2/4 start empty scratch database and object store"
docker rm -f rt-db rt-s3 >/dev/null 2>&1 || true
docker run -d --rm --name rt-db --network $NET -e POSTGRES_USER="$POSTGRES_USER" -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e POSTGRES_DB="$POSTGRES_DB" postgres:16-alpine >/dev/null
docker run -d --rm --name rt-s3 --network $NET --entrypoint /bin/sh chrislusf/seaweedfs:4.47 -c \
  "printf '{\"identities\":[{\"name\":\"admin\",\"credentials\":[{\"accessKey\":\"%s\",\"secretKey\":\"%s\"}],\"actions\":[\"Admin\",\"Read\",\"Write\",\"List\"]}]}' \"$S3_ADMIN_ACCESS_KEY\" \"$S3_ADMIN_SECRET\" > /tmp/s3.json && exec weed server -dir=/data -ip=rt-s3 -s3 -s3.config=/tmp/s3.json -master.volumeSizeLimitMB=512 -volume.max=50" >/dev/null
until docker exec rt-db pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 1; done
until docker exec rt-s3 wget -q -O /dev/null http://127.0.0.1:8333/healthz 2>/dev/null; do sleep 1; done

echo "3/4 restore $STAMP into the scratch services"
$C run --rm -e POSTGRES_HOST=rt-db -e RCLONE_CONFIG_S3_ENDPOINT=http://rt-s3:8333 backup restore "$STAMP" | tail -2

echo "4/4 compare"
q="select 'users',count(*) from users union all select 'cases',count(*) from cases union all select 'predictions',count(*) from predictions union all select 'reviews',count(*) from reviews union all select 'audit_log',count(*) from audit_log order by 1"
live=$($C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$q")
restored=$(docker exec rt-db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$q")
count_objects() { docker run --rm --network $NET -e RCLONE_CONFIG_X_TYPE=s3 -e RCLONE_CONFIG_X_PROVIDER=SeaweedFS \
  -e RCLONE_CONFIG_X_ENDPOINT="http://$1:8333" -e RCLONE_CONFIG_X_ACCESS_KEY_ID="$S3_ADMIN_ACCESS_KEY" \
  -e RCLONE_CONFIG_X_SECRET_ACCESS_KEY="$S3_ADMIN_SECRET" --entrypoint rclone gleasonai/backup:latest size "x:$S3_BUCKET" --json; }
live_obj=$(count_objects s3); restored_obj=$(count_objects rt-s3)
docker rm -f rt-db rt-s3 >/dev/null
echo "database   live: $(echo $live | tr '\n' ' ')"
echo "database restored: $(echo $restored | tr '\n' ' ')"
echo "objects    live: $live_obj"
echo "objects restored: $restored_obj"
if [ "$live" = "$restored" ] && [ "$live_obj" = "$restored_obj" ]; then echo "RESTORE TEST PASSED ($STAMP)"; else echo "RESTORE TEST FAILED"; exit 1; fi
