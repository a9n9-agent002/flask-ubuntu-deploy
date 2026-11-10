#!/usr/bin/env bash
# deploy.sh — put notes-api on a fresh Ubuntu 24.04 Droplet, and keep it deployable afterwards.
#
#   ./deploy.sh bootstrap --host 203.0.113.10 --domain notes.example.com   # once
#   ./deploy.sh deploy    --host 203.0.113.10                              # every release
#   ./deploy.sh rollback  --host 203.0.113.10                              # undo the last release
#   ... add --dry-run to any of them to print the commands instead of running them.
#
# Assumes: Ubuntu 24.04, root SSH key access to --host, DNS for --domain already pointing at the
# Droplet's IP (Caddy needs that before it can issue a certificate). Releases are streamed as a tar
# archive over the SSH connection, so nothing else has to be installed on either end.
# Layout on the server: releases are immutable directories, `current` is a symlink, and the
# database lives outside both so a deploy can never touch it.
set -euo pipefail

HOST=""; DOMAIN=""; DRY=0; ACTION=""
while [ $# -gt 0 ]; do
  case "$1" in
    bootstrap|deploy|rollback) ACTION="$1"; shift ;;
    --host)   HOST="${2:?--host needs a value}"; shift 2 ;;
    --domain) DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ACTION" ] || { echo "usage: $0 bootstrap|deploy|rollback --host IP [--domain NAME] [--dry-run]" >&2; exit 2; }
[ -n "$HOST" ]   || { echo "--host is required" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
REMOTE="/opt/notes-api"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

run() {   # run a local command, or print it
  if [ "$DRY" = 1 ]; then printf 'DRY  %s\n' "$*"; else "$@"; fi
}
remote() {   # run a shell script on the server over SSH
  if [ "$DRY" = 1 ]; then
    printf 'DRY  ssh root@%s <<'"'"'REMOTE'"'"'\n' "$HOST"
    sed 's/^/     | /' <<< "$1"
    printf 'DRY  REMOTE\n'
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$HOST" "bash -s" <<< "$1"
  fi
}

bootstrap() {
  [ -n "$DOMAIN" ] || { echo "bootstrap needs --domain (Caddy will not get a certificate without it)" >&2; exit 2; }
  # Ship the two config files first, because the remote block below installs them from /tmp.
  run scp -q "$HERE/scripts/notes-api.service" "$HERE/scripts/Caddyfile" "root@$HOST:/tmp/"
  remote "$(cat <<REMOTE
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# 1. Packages. Caddy from its own apt repo, because the Ubuntu archive is far behind.
apt-get update -qq
apt-get install -y -qq python3 ufw curl gpg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

# 2. A service account that owns the data and nothing else.
id -u notes >/dev/null 2>&1 || useradd --system --home /var/lib/notes-api --shell /usr/sbin/nologin notes
install -d -o notes -g notes -m 0750 /var/lib/notes-api
install -d -m 0755 $REMOTE/releases
# Caddy runs as the `caddy` user and must own its log directory, or it exits on first write.
getent passwd caddy >/dev/null && install -d -o caddy -g caddy -m 0750 /var/log/caddy || install -d -m 0750 /var/log/caddy

# 3. Firewall: SSH, HTTP and HTTPS. The app port 8000 stays closed on purpose.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

# 4. Unit and reverse proxy.
install -m 0644 /tmp/notes-api.service /etc/systemd/system/notes-api.service
install -m 0644 /tmp/Caddyfile /etc/caddy/Caddyfile
sed -i 's/{[^}]*DOMAIN[^}]*}/$DOMAIN/' /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now notes-api
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

echo "bootstrap done on \$(hostname): caddy=\$(caddy version | head -1)"
REMOTE
)"
}

deploy() {
  RELEASE="$REMOTE/releases/$STAMP"
  remote "set -euo pipefail; install -d -o notes -g notes $RELEASE/app"
  # No rsync on either end: a tar stream over the SSH connection already open for the commands.
  if [ "$DRY" = 1 ]; then
    printf 'DRY  tar czf - -C %s/app . | ssh root@%s "tar xzf - -C %s/app"\n' "$HERE" "$HOST" "$RELEASE"
  else
    tar czf - -C "$HERE/app" . | ssh -o BatchMode=yes "root@$HOST" "tar xzf - -C $RELEASE/app && chown -R notes:notes $RELEASE"
  fi
  remote "$(cat <<REMOTE
set -euo pipefail
ln -sfn $RELEASE $REMOTE/current
systemctl restart notes-api
sleep 1
systemctl is-active --quiet notes-api || { journalctl -u notes-api -n 30 --no-pager; exit 1; }
curl -fsS http://127.0.0.1:8000/healthz && echo " <- health check passed on the server"
ls -1 $REMOTE/releases | head -n -3 | while read -r old; do rm -rf "$REMOTE/releases/\$old"; done
REMOTE
)"
  echo "deployed $STAMP to $HOST"
}

rollback() {
  remote "$(cat <<REMOTE
set -euo pipefail
prev=\$(ls -1dt $REMOTE/releases/*/ | sed -n 2p)
[ -n "\$prev" ] || { echo "no previous release to roll back to" >&2; exit 1; }
ln -sfn "\${prev%/}" $REMOTE/current
systemctl restart notes-api
sleep 1
systemctl is-active --quiet notes-api && echo "rolled back to \${prev%/}"
REMOTE
)"
}

[ -f "$HERE/app/server.py" ] || { echo "run this from the repository root (app/server.py not found)" >&2; exit 2; }
case "$ACTION" in
  bootstrap) bootstrap ;;
  deploy)    deploy ;;
  rollback)  rollback ;;
esac
