#!/bin/sh
# Apply database migrations, then start the given command (uvicorn by default).
set -e
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  alembic -c /app/backend/alembic.ini upgrade head
fi
exec "$@"
