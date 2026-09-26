#!/bin/bash
# Run backup.sh every night at BACKUP_HOUR (UTC); `backup` or `restore <stamp>` run once.
set -euo pipefail
case "${1:-schedule}" in
  backup) exec /usr/local/bin/backup.sh ;;
  restore) shift; exec /usr/local/bin/restore.sh "$@" ;;
  schedule)
    echo "backup scheduler: nightly at ${BACKUP_HOUR:-1}:30 UTC, keeping ${BACKUP_KEEP_DAYS:-14} days"
    while true; do
      now=$(date -u +%s)
      next=$(date -u -d "$(date -u +%Y-%m-%d) ${BACKUP_HOUR:-1}:30" +%s 2>/dev/null || echo 0)
      [ "$next" -le "$now" ] && next=$((next + 86400))
      sleep $((next - now))
      /usr/local/bin/backup.sh || echo "BACKUP FAILED at $(date -u)" >&2
    done ;;
  *) exec "$@" ;;
esac
