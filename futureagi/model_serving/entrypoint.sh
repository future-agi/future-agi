#!/bin/sh
# entrypoint.sh

GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-300}"

echo "Starting FastAPI application with Gunicorn (timeout=${GUNICORN_TIMEOUT}s)..."

exec gunicorn -w 1 -t "${GUNICORN_TIMEOUT}" -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8080 app.main:app
