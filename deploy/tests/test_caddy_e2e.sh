#!/usr/bin/env bash
# tests/test_caddy_e2e.sh — prove the reverse-proxy leg actually works, not just that the file parses.
#
# Starts app/server.py on a free port, starts a real Caddy in front of it on another free port
# with the same directives the production Caddyfile uses (minus TLS, which needs a public name),
# and exercises the app through Caddy: health, create, list, 404, and the access log.
#
#   CADDY=/path/to/caddy ./tests/test_caddy_e2e.sh      # exit 0 = pass
# Requires a caddy binary; skips (exit 0 with a message) if there is none.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
CADDY="${CADDY:-$(command -v caddy || true)}"
[ -n "$CADDY" ] || { echo "SKIP: no caddy binary (set CADDY=/path/to/caddy)"; exit 0; }

freeport() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
APP_PORT="$(freeport)"; WEB_PORT="$(freeport)"
TMP="$(mktemp -d)"
fail=0
check() { if [ "$1" = "$2" ]; then echo "  ok   $3 ($1)"; else echo "  FAIL $3 (got $1, want $2)"; fail=1; fi; }

cleanup() { [ -n "${APP_PID:-}" ] && kill "$APP_PID" 2>/dev/null; [ -n "${CADDY_PID:-}" ] && kill "$CADDY_PID" 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

# 1. the config the deploy guide installs must validate
DOMAIN=notes.example.com CADDY_LOG="$TMP/caddy-validate.log" \
  "$CADDY" validate --config "$ROOT/scripts/Caddyfile" --adapter caddyfile >"$TMP/validate.log" 2>&1
check "$?" "0" "caddy validate scripts/Caddyfile"

# 2. a Caddy in front of the real app
cat >"$TMP/Caddyfile" <<EOF
{
	admin off
	auto_https off
}
:$WEB_PORT {
	encode zstd gzip
	reverse_proxy 127.0.0.1:$APP_PORT
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		-Server
	}
	log {
		output file $TMP/access.log
		format json
	}
	respond /security.txt "Contact: mailto:security@example.com\n" 200
}
EOF

NOTES_PORT="$APP_PORT" NOTES_DB="$TMP/notes.db" python3 "$ROOT/app/server.py" >"$TMP/app.log" 2>&1 &
APP_PID=$!
"$CADDY" run --config "$TMP/Caddyfile" --adapter caddyfile >"$TMP/caddy.log" 2>&1 &
CADDY_PID=$!

for _ in $(seq 1 100); do
  curl -fsS "http://127.0.0.1:$WEB_PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.1
done

BASE="http://127.0.0.1:$WEB_PORT"
check "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")" "200" "GET /healthz through Caddy"
check "$(curl -s "$BASE/healthz" | python3 -c 'import json,sys;print(json.load(sys.stdin)["notes"])')" "0" "health says zero notes"

check "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"text":"via caddy"}' "$BASE/notes")" "201" "POST /notes through Caddy"
check "$(curl -s "$BASE/notes" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["notes"]))')" "1" "GET /notes lists it"
check "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/nope")" "404" "unknown route is 404"
check "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/security.txt")" "200" "security.txt served by Caddy, not the app"

# the headers the Caddyfile promises must actually arrive
HDRS="$(curl -sI "$BASE/healthz")"
check "$(printf '%s' "$HDRS" | grep -ci 'strict-transport-security')" "1" "HSTS header present"
check "$(printf '%s' "$HDRS" | grep -ci '^server:')" "0" "Server header removed"

# and the access log is being written, which is the thing you read when something breaks
sleep 0.3
ENTRIES="$(grep -c '"request"' "$TMP/access.log" 2>/dev/null || true)"
[ -n "$ENTRIES" ] || ENTRIES=0
if [ "$ENTRIES" -ge 1 ]; then echo "  ok   Caddy access log has entries ($ENTRIES)"; else echo "  FAIL Caddy access log is empty"; fail=1; fi

[ "$fail" = 0 ] && echo "ALL PASS" || echo "FAILURES ABOVE"
exit "$fail"
