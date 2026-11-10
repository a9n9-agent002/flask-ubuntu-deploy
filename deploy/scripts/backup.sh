#!/usr/bin/env bash
# backup.sh — a consistent copy of the SQLite database, on a timer.
#
#   sudo install -m 0755 deploy/backup.sh /usr/local/bin/notes-api-backup
#   sudo install -d -m 0700 /var/backups/notes-api
#   sudo crontab -e   ->   15 * * * * /usr/local/bin/notes-api-backup >> /var/log/notes-api-backup.log 2>&1
#
# Why not `cp notes.db`: a plain copy of a live SQLite file can catch it mid-transaction, and with
# WAL enabled the .db file alone is not the whole database. `sqlite3 .backup` uses SQLite's own
# online-backup API, which takes a read lock and produces a consistent single file.
set -euo pipefail

DB="${NOTES_DB:-/var/lib/notes-api/notes.db}"
DEST="${BACKUP_DIR:-/var/backups/notes-api}"
KEEP="${KEEP:-7}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/notes-$STAMP.db"

install -d -m 0700 "$DEST"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$OUT'"
else
  # python3 is installed on the Droplet anyway; the stdlib has the same API.
  python3 - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PY
fi
gzip -9 "$OUT"

# An integrity check on the copy, not on the original: a backup you have never opened is a hope.
python3 - "$OUT.gz" <<'PY'
import gzip, shutil, sqlite3, sys, tempfile, os
src = sys.argv[1]
tmp = tempfile.mktemp(suffix=".db")
with gzip.open(src, "rb") as f, open(tmp, "wb") as o:
    shutil.copyfileobj(f, o)
con = sqlite3.connect(tmp)
print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0],
      "| notes:", con.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
con.close(); os.unlink(tmp)
PY

ls -1t "$DEST"/notes-*.db.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "$(date -u +%FT%TZ) backup ok -> $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"
