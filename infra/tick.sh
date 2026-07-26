#!/bin/sh
# The scheduled drain. Calls POST /api/internal/tick once an hour, forever.
#
# THIS IS A FILE AND NOT A START COMMAND, and that is the whole point of it. Railway's
# `RAILWAY_RUN_COMMAND` was ignored outright on this service — the container started, the image's
# own entrypoint ran instead, and `railway status` said "Completed" while nothing ticked. A
# service whose behaviour lives in a dashboard field is a service nobody can reproduce from the
# repository; baking the loop into the image makes it `git log`-able and removes the one step of
# this deployment that could not be proven from source.
#
# `set -e` is deliberately ABSENT. A failed tick must not stop the loop: the next hour is the
# retry, and there is nothing to clean up, because every message this tick did not reach still
# has `extracted_at IS NULL`.

: "${INTERNAL_TICK_SECRET:?INTERNAL_TICK_SECRET is not set — every tick would 401 silently}"

PENNY_URL="${PENNY_TICK_URL:-http://penny.railway.internal:8000/api/internal/tick}"
# MUST EXCEED TICK_DEADLINE_SECONDS in backend/app/routers/internal.py. A curl that gives up while
# the tick is still working leaves the work committed and the log saying "timeout" on every
# healthy run, which is how a real failure stops being noticed.
TIMEOUT="${PENNY_TICK_TIMEOUT:-900}"
INTERVAL="${PENNY_TICK_INTERVAL:-3600}"

echo "tick: every ${INTERVAL}s -> ${PENNY_URL}"

while true; do
  # `-f` is what makes a misconfigured secret audible: without it curl exits 0 on a 401 and
  # prints the error envelope nowhere anybody reads.
  curl -fsS -m "$TIMEOUT" -X POST \
    -H "X-Penny-Tick-Secret: $INTERNAL_TICK_SECRET" \
    "$PENNY_URL" || echo "tick: failed (exit $?) — retrying in ${INTERVAL}s"
  sleep "$INTERVAL"
done
