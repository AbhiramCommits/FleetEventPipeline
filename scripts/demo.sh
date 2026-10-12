#!/usr/bin/env bash
# End-to-end demo: migrate -> reset -> api -> handler -> ingest -> elt -> dq -> report.
# Starts the mock vendor API and the Event Grid handler locally, runs the full
# pipeline and tears the background processes down on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

export DEMO_STARTED_AT
DEMO_STARTED_AT="$("$PY" -c 'import time; print(time.time())')"
export MOCKAPI_FAILURE_RATE="${MOCKAPI_FAILURE_RATE:-0.03}"
LOOKBACK_HOURS="${LOOKBACK_HOURS:-720}"

cleanup() {
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${HANDLER_PID:-}" ] && kill "$HANDLER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "==> migrate"
"$PY" -m src.common.migrate

echo "==> reset pipeline data (clean, reproducible demo)"
"$PY" -m src.common.reset

echo "==> start mock vendor API (:8099)"
"$PY" -m uvicorn src.mockapi.app:app --host 0.0.0.0 --port 8099 >/tmp/fleet_api.log 2>&1 &
API_PID=$!

echo "==> start Event Grid handler (:7099)"
node handler/src/server.js >/tmp/fleet_handler.log 2>&1 &
HANDLER_PID=$!

"$PY" scripts/wait_for_http.py http://localhost:8099/health 60
"$PY" scripts/wait_for_http.py http://localhost:7099/health 60

echo "==> ingest (lookback ${LOOKBACK_HOURS}h)"
"$PY" -m src.ingest.job --source all --lookback-hours "$LOOKBACK_HOURS"

echo "==> elt"
"$PY" -m src.elt.run

echo "==> dq"
"$PY" -m src.dq.checks

export DEMO_ENDED_AT
DEMO_ENDED_AT="$("$PY" -c 'import time; print(time.time())')"

echo "==> report"
"$PY" -m src.analysis.report

echo "==> demo complete"
