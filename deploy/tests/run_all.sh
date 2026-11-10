#!/usr/bin/env bash
# run_all.sh — every check in this repository, in order, with one exit code.
#   bash tests/run_all.sh          (CADDY=/path/to/caddy to include the proxy test)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
fail=0

echo "== app tests"; python3 "$HERE/test_app.py" 2>&1 | tail -3 || fail=1
python3 "$HERE/test_app.py" >/dev/null 2>&1 || fail=1

echo "== shell syntax"; for f in "$ROOT/deploy.sh" "$ROOT/scripts/backup.sh" "$HERE/test_caddy_e2e.sh"; do
  bash -n "$f" && echo "  ok   $(basename "$f")" || { echo "  FAIL $(basename "$f")"; fail=1; }
done

echo "== SIZING.md is current"; python3 "$ROOT/render_sizing.py" --check || fail=1

echo "== deploy dry-run"; bash "$ROOT/deploy.sh" deploy --host 203.0.113.10 --dry-run >/dev/null && echo "  ok   dry-run" || { echo "  FAIL dry-run"; fail=1; }

echo "== caddy + proxy end to end"; bash "$HERE/test_caddy_e2e.sh" || fail=1

echo "== wsgi (Flask) path from the README"; bash "$HERE/test_wsgi.sh" || fail=1

[ "$fail" = 0 ] && echo "ALL CHECKS PASS" || echo "SOME CHECKS FAILED"
exit "$fail"
