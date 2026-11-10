#!/usr/bin/env bash
# test_wsgi.sh — runs the README's "If your app is Flask or Django" paragraph.
#
# The paragraph tells a reader to swap one ExecStart line for a WSGI server and keep the rest of
# the unit. This script starts that exact process (gunicorn, sync workers, 127.0.0.1) against
# app/wsgi_example.py at two worker counts, checks it answers, and prints the resident memory of
# master + workers each time — the number that decides whether a Flask app still fits the plan
# `SIZING.md` picks, and what a fourth worker costs.
#
#   bash tests/test_wsgi.sh
# Skips loudly (exit 0, message) when Flask or gunicorn is not installed, because the rest of the
# suite has no third-party dependency and must keep running on a bare interpreter.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")/app"

if ! python3 -c "import flask" >/dev/null 2>&1; then
  echo "  SKIP wsgi path: flask is not installed (pip install flask gunicorn)"
  exit 0
fi
if ! python3 -m gunicorn --version >/dev/null 2>&1; then
  echo "  SKIP wsgi path: gunicorn is not installed (pip install flask gunicorn)"
  exit 0
fi

cd "$APP" || exit 1

run_with_workers() {
  local workers="$1" port="$2"
  local url="http://127.0.0.1:${port}/healthz"
  local master ok=0

  python3 -m gunicorn --workers "$workers" --bind "127.0.0.1:${port}" \
      --log-level warning wsgi_example:app &
  master=$!
  cleanup_one() { kill -TERM "$master" 2>/dev/null; wait "$master" 2>/dev/null; }
  trap cleanup_one RETURN

  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "$url" >"/tmp/wsgi_health.$$" 2>/dev/null; then ok=1; break; fi
    kill -0 "$master" 2>/dev/null || break
    sleep 0.25
  done
  if [ "$ok" != 1 ]; then
    echo "  FAIL wsgi path: no answer on $url within 15 s ($workers workers)"; return 1
  fi

  local body; body="$(cat "/tmp/wsgi_health.$$")"
  case "$body" in
    *'"ok":true'*) echo "  ok   ${workers}w GET /healthz -> $body" ;;
    *) echo "  FAIL wsgi path: unexpected body: $body"; return 1 ;;
  esac

  # Routing, not just the health endpoint.
  local code note
  code="$(curl -s -o "/tmp/wsgi_note.$$" -w '%{http_code}' --max-time 2 "http://127.0.0.1:${port}/notes/7")"
  note="$(cat "/tmp/wsgi_note.$$")"
  if [ "$code" = "200" ] && [ -n "$note" ]; then
    echo "  ok   ${workers}w GET /notes/7 -> $code $note"
  else
    echo "  FAIL wsgi path: GET /notes/7 -> $code $note"; return 1
  fi
  rm -f "/tmp/wsgi_health.$$" "/tmp/wsgi_note.$$"

  # Warm every worker before measuring: a worker that has never served a request has not yet touched
  # its copy-on-write pages, so measuring straight after start-up understates the real footprint.
  for _ in $(seq 1 $((workers * 4))); do curl -fsS --max-time 2 "$url" >/dev/null 2>&1 || true; done

  # Resident memory of the master and its workers (VmRSS, KiB), summed.
  local rss=0 pid kb
  for pid in "$master" $(pgrep -P "$master" 2>/dev/null); do
    kb="$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null)"
    [ -n "${kb:-}" ] && rss=$((rss + kb))
  done
  if [ "$rss" -le 0 ]; then echo "  FAIL wsgi path: could not read VmRSS"; return 1; fi
  echo "  ok   ${workers}w resident: $((rss / 1024)) MiB across master + $workers workers"
  return 0
}

fail=0
run_with_workers 2 8011 || fail=1
run_with_workers 4 8012 || fail=1
if [ "$fail" = 0 ]; then
  echo "  ok   wsgi path: the README's ExecStart line runs (2 and 4 workers)"
else
  echo "  FAIL wsgi path"
fi
exit "$fail"
