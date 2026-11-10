#!/usr/bin/env python3
"""wsgi_example.py — a 15-line WSGI app, here only to prove the framework path in the README.

This is deliberately NOT a second service. `app/server.py` is the service this repo deploys;
this file exists so that the paragraph in the README that says "change ExecStart to a WSGI
server and keep the rest of the unit" is a measured statement rather than a snippet nobody ran:
`tests/test_wsgi.sh` starts exactly the process that ExecStart would start, asks it for
/healthz, and reads its resident memory.

    ExecStart=/opt/notes-api/venv/bin/gunicorn wsgi_example:app --bind 127.0.0.1:8000

Requires Flask (and gunicorn, in the venv). Nothing else in `deploy/` depends on either.
"""
from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, app="wsgi_example", runtime="wsgi")


# A Flask route with a path parameter, so the test proves routing works and not just "/".
@app.get("/notes/<int:note_id>")
def note(note_id: int):
    return jsonify(id=note_id, text="example", source="wsgi_example")


if __name__ == "__main__":
    # Same host/port convention as server.py: bind loopback, let Caddy face the internet.
    app.run(host="127.0.0.1", port=8000)
