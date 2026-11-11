"""App for the reload-vs-restart experiment.

/version returns the VERSION constant read at import time, so a request that
answers with a new value proves the new code is actually serving (a reload that
only restarted workers but re-imported the same file would look identical).

/slow?s=SECONDS blocks for that long, to represent a real request in flight
(a report query, an upload) that a deploy must not cut off.
"""
import time

from flask import Flask, request

VERSION = "v1"

app = Flask(__name__)


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/slow")
def slow():
    s = float(request.args.get("s", "1.5"))
    time.sleep(s)
    return {"version": VERSION, "slept": s}
