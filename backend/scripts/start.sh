#!/bin/sh
# Production process start (container / Render / VM). Does NOT migrate the
# database: run `python scripts/db_admin.py upgrade` as a separate deployment
# step first; the app refuses to start on an out-of-date schema.
#   PORT             listen port (default 8000)
#   WEB_CONCURRENCY  worker processes (default 2). Rate limits and the heavy-
#                    computation semaphore are per process (docs/SECURITY.md).
#   IIP_GRACEFUL_S   seconds to finish in-flight requests on SIGTERM (default 30)
set -e
cd "$(dirname "$0")/.."
exec python -m uvicorn main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
  --timeout-graceful-shutdown "${IIP_GRACEFUL_S:-30}" \
  --timeout-keep-alive 5 \
  --no-server-header \
  --log-level warning
