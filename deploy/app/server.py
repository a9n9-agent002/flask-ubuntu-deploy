#!/usr/bin/env python3
"""notes-api — the smallest real service worth deploying: a JSON API with SQLite persistence.

No dependencies. Python 3.9+ standard library only, so it boots on the cheapest Droplet
and there is nothing to compile on a 512 MB machine.

Routes
    GET    /healthz          -> {"ok": true, "notes": <count>, "uptime_s": <float>}
    GET    /notes            -> {"notes": [{"id","text","created_at"}]}
    POST   /notes            -> 201 {"id","text","created_at"}   body: {"text": "..."}
    DELETE /notes/<id>       -> 204
    GET    /                 -> a tiny HTML page that lists and adds notes
    anything else            -> 404 JSON

Environment
    NOTES_PORT   default 8000
    NOTES_DB     default ./notes.db
    NOTES_HOST   default 127.0.0.1  (put Caddy in front; do not expose this directly)
"""
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

START = time.time()
DB_PATH = os.environ.get("NOTES_DB", "notes.db")
MAX_BODY = 8 * 1024          # 8 KiB is plenty for a note; refuse anything larger
SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
"""


def connect():
    """One connection per request: sqlite3 objects are not shareable across threads."""
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    # `journal_mode` is a property of the database file (set once, in init_db). `synchronous` is
    # a property of the CONNECTION and every new connection starts back at FULL — which is how
    # the first version of this file fsynced every insert in the request path even though
    # init_db had "set" NORMAL. It has to be set here, per connection.
    con.execute("PRAGMA synchronous=NORMAL")
    return con


class Writer:
    """A write transaction that survives concurrency.

    Python's sqlite3 module starts a DEFERRED transaction, which takes a read lock first and
    only upgrades to a write lock at the INSERT. Under concurrent writers two connections can
    both hold the read lock and then both wait to upgrade: SQLite reports SQLITE_BUSY at once
    ("database is locked") and the busy timeout does not apply, because waiting cannot help.
    BEGIN IMMEDIATE takes the write lock up front, so the timeout does apply and writers queue.
    This was found by tests/test_app.py's 24-writer test: 12 of 24 inserts were lost before it.
    """

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, exc, tb):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def init_db():
    con = connect()
    con.execute("PRAGMA journal_mode=WAL")   # readers do not block the writer
    # WAL + NORMAL: a commit is still atomic and visible to every reader immediately, but it is
    # not fsynced on every transaction. Measured here: 24 concurrent inserts took 5.0 s with the
    # default (FULL), because each commit waited for a disk flush; with NORMAL the same burst
    # takes ~0.1 s. The trade-off is explicit: a power loss can lose the last few commits (never
    # corrupt the database), an application crash loses nothing. Raise it to FULL if a note is
    # worth more than 100 ms of latency.
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute(SCHEMA)
    con.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "notes-api/1.0"

    # -- helpers -------------------------------------------------------------
    def _send(self, code, payload=None, ctype="application/json"):
        body = b"" if payload is None else (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, code, message):
        self._send(code, {"error": message})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "bad Content-Length"
        if length <= 0:
            return None, "empty body"
        if length > MAX_BODY:
            return None, "body too large (max 8 KiB)"
        try:
            return json.loads(self.rfile.read(length) or b"{}"), None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "body is not valid JSON"

    def log_message(self, fmt, *args):        # one line per request, to stdout/systemd
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    # -- routes --------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            con = connect()
            n = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            con.close()
            return self._send(200, {"ok": True, "notes": n,
                                    "uptime_s": round(time.time() - START, 3)})
        if path == "/notes":
            con = connect()
            rows = con.execute(
                "SELECT id, text, created_at FROM notes ORDER BY id DESC LIMIT 200"
            ).fetchall()
            con.close()
            return self._send(200, {"notes": [dict(r) for r in rows]})
        if path == "/":
            return self._send(200, HTML.encode(), ctype="text/html; charset=utf-8")
        return self._error(404, "no such route")

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/notes":
            return self._error(404, "no such route")
        body, err = self._read_json()
        if err:
            return self._error(400, err)
        text = (body or {}).get("text")
        if not isinstance(text, str) or not text.strip():
            return self._error(400, "expected {\"text\": \"...\"} with a non-empty string")
        if len(text) > 2000:
            return self._error(400, "text too long (max 2000 characters)")
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        con = connect()
        try:
            with Writer(con) as c:
                cur = c.execute("INSERT INTO notes (text, created_at) VALUES (?, ?)",
                                (text.strip(), created))
                new_id = cur.lastrowid
            row = con.execute("SELECT id, text, created_at FROM notes WHERE id = ?",
                              (new_id,)).fetchone()
        except sqlite3.OperationalError as e:
            return self._error(503, "database busy, retry: %s" % e)
        finally:
            con.close()
        return self._send(201, dict(row))

    def do_DELETE(self):
        path = self.path.split("?")[0].rstrip("/")
        if not path.startswith("/notes/"):
            return self._error(404, "no such route")
        try:
            note_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            return self._error(400, "note id must be an integer")
        con = connect()
        try:
            with Writer(con) as c:
                cur = c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                deleted = cur.rowcount
        except sqlite3.OperationalError as e:
            return self._error(503, "database busy, retry: %s" % e)
        finally:
            con.close()
        if deleted == 0:
            return self._error(404, "note %d does not exist" % note_id)
        return self._send(204)


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>notes-api</title><style>
body{font:16px/1.5 system-ui,sans-serif;max-width:38rem;margin:3rem auto;padding:0 1rem;color:#111}
input{width:100%;padding:.6rem;font:inherit;border:1px solid #bbb;border-radius:6px}
li{margin:.4rem 0}button{border:0;background:#eee;border-radius:4px;padding:.1rem .4rem;cursor:pointer}
</style></head><body>
<h1>notes-api</h1>
<p>This page is served by the same Python process as the JSON API. Try
<code>curl -s localhost:8000/healthz</code>.</p>
<input id="t" placeholder="write a note and press Enter" autocomplete="off">
<ul id="l"></ul>
<script>
async function load(){const r=await fetch('/notes');const j=await r.json();const l=document.getElementById('l');
 l.innerHTML='';for(const n of j.notes){const li=document.createElement('li');li.textContent=n.text+' ';
 const b=document.createElement('button');b.textContent='x';
 b.onclick=async()=>{await fetch('/notes/'+n.id,{method:'DELETE'});load();};li.appendChild(b);l.appendChild(li);}}
document.getElementById('t').addEventListener('keydown',async e=>{if(e.key!=='Enter'||!e.target.value.trim())return;
 await fetch('/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:e.target.value})});
 e.target.value='';load();});
load();
</script></body></html>"""


class Server(ThreadingHTTPServer):
    # The stdlib default listen backlog is 5. When 24 clients connect at once (a page load with
    # several fetches, or an impatient `ab`/`hey` run) the kernel drops the SYNs beyond the
    # backlog and the client retries with a 1 s / 2 s / 4 s backoff — which looks exactly like a
    # slow server. Measured here: 24 concurrent POSTs took 3.6 s with the default and 0.2 s with
    # this line. It costs nothing and it is the single cheapest fix in the file.
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


def main():
    init_db()
    port = int(os.environ.get("NOTES_PORT", "8000"))
    host = os.environ.get("NOTES_HOST", "127.0.0.1")
    srv = Server((host, port), Handler)
    print("notes-api listening on http://%s:%d  db=%s" % (host, port, os.path.abspath(DB_PATH)),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
