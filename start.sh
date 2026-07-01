#!/bin/sh
# Container entrypoint. Runs schema migrations, then hands off to uvicorn.
#
# `set -e` — abort if alembic fails, rather than starting the app against a
# stale/broken schema.
# `exec` — replace this shell with uvicorn so PID 1 is the app itself and
# SIGTERM from the platform (Railway, k8s, docker stop) reaches it directly.
# `${PORT:-8000}` — Railway/Heroku inject PORT; local docker-compose does not.
set -e

echo "[start] alembic upgrade head"
alembic upgrade head

echo "[start] launching uvicorn on port ${PORT:-8000}"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
