#!/usr/bin/env python3
"""End-to-end tests for notes-api: start the real server as a subprocess, talk to it over HTTP.

Run:  python3 tests/test_app.py      (exit 0 = all pass)
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "app", "server.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, db):
        self.db = db
        self.port = free_port()
        self.proc = None

    def start(self):
        env = dict(os.environ, NOTES_PORT=str(self.port), NOTES_DB=self.db,
                   NOTES_HOST="127.0.0.1")
        self.proc = subprocess.Popen([sys.executable, SERVER], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                self.req("GET", "/healthz")
                return self
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("server did not start: " + self.proc.stdout.read().decode()[-800:])

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None

    def req(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                payload = resp.read()
                return resp.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, payload


class TestNotesApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "notes.db")
        self.s = Server(self.db).start()

    def tearDown(self):
        self.s.stop()

    def test_healthz_reports_zero_notes_on_a_fresh_db(self):
        code, body = self.s.req("GET", "/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["notes"], 0)

    def test_create_then_list(self):
        code, note = self.s.req("POST", "/notes", {"text": "hello droplet"})
        self.assertEqual(code, 201)
        self.assertEqual(note["text"], "hello droplet")
        self.assertIn("created_at", note)
        code, body = self.s.req("GET", "/notes")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["notes"]), 1)
        self.assertEqual(body["notes"][0]["id"], note["id"])

    def test_survives_a_restart_because_it_is_sqlite(self):
        self.s.req("POST", "/notes", {"text": "persisted"})
        self.s.stop()
        self.s = Server(self.db).start()
        code, body = self.s.req("GET", "/notes")
        self.assertEqual(code, 200)
        self.assertEqual([n["text"] for n in body["notes"]], ["persisted"])
        # and the file is a real sqlite database, readable by the sqlite3 CLI
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
        con.close()

    def test_delete(self):
        _, note = self.s.req("POST", "/notes", {"text": "delete me"})
        code, _ = self.s.req("DELETE", "/notes/%d" % note["id"])
        self.assertEqual(code, 204)
        code, body = self.s.req("DELETE", "/notes/%d" % note["id"])
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "note %d does not exist" % note["id"])

    def test_rejects_bad_input_without_crashing(self):
        self.assertEqual(self.s.req("POST", "/notes", raw=b"{not json")[0], 400)
        self.assertEqual(self.s.req("POST", "/notes", {"txt": "wrong key"})[0], 400)
        self.assertEqual(self.s.req("POST", "/notes", {"text": "  "})[0], 400)
        self.assertEqual(self.s.req("POST", "/notes", {"text": "x" * 2001})[0], 400)
        self.assertEqual(self.s.req("POST", "/notes", raw=b'{"text":"' + b"y" * 9000 + b'"}')[0], 400)
        self.assertEqual(self.s.req("GET", "/nope")[0], 404)
        self.assertEqual(self.s.req("DELETE", "/notes/abc")[0], 400)
        # still alive after all of that
        self.assertEqual(self.s.req("GET", "/healthz")[0], 200)

    def test_index_page_is_html(self):
        r = urllib.request.Request("http://127.0.0.1:%d/" % self.s.port)
        with urllib.request.urlopen(r, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b"notes-api", resp.read())

    def test_concurrent_writers_do_not_lose_notes(self):
        def post(i):
            self.s.req("POST", "/notes", {"text": "n%d" % i})
        threads = [threading.Thread(target=post, args=(i,)) for i in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, body = self.s.req("GET", "/notes")
        self.assertEqual(len(body["notes"]), 24)
        self.assertEqual(body["notes"][0]["text"][0], "n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
