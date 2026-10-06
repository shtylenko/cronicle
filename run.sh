#!/bin/sh
# cronicle launcher: always serves http://127.0.0.1:8231
# If the port is busy, the occupant(s) are stopped first so this app keeps its port.
PORT="${CRONICLE_PORT:-8231}"
tries=0
while [ "$tries" -lt 5 ]; do
  pids="$(lsof -ti:"$PORT" 2>/dev/null)"
  [ -z "$pids" ] && break
  # shellcheck disable=SC2086 word splitting intended: one PID per line
  echo "Port $PORT busy (PID(s): $pids) — stopping…"
  kill $pids 2>/dev/null
  sleep 1
  tries=$((tries + 1))
done
still="$(lsof -ti:"$PORT" 2>/dev/null)"
if [ -n "$still" ]; then
  echo "Force-killing PID(s): $still"
  kill -9 $still 2>/dev/null
  sleep 1
  still="$(lsof -ti:"$PORT" 2>/dev/null)"
fi
if [ -n "$still" ]; then
  echo "ERROR: port $PORT still busy (PID(s): $still) — refusing to start" >&2
  exit 1
fi
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/cronicle ]; then
  exec .venv/bin/cronicle serve --host 127.0.0.1 --port "$PORT"
else
  exec cronicle serve --host 127.0.0.1 --port "$PORT"
fi
