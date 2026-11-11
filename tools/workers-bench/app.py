"""Minimal Flask app used to measure what gunicorn worker count actually buys.

Three endpoints, chosen to separate the things a worker count trades off:
  /io   20 ms of blocked time (a database call, an API call)   -> I/O-bound
  /cpu  a FIXED amount of computation (~10 ms of CPU when it has a core to
        itself) -> CPU-bound. Fixed iterations, not a wall-clock loop: a
        wall-clock burn finishes in 10 ms even when sharing a core, which
        would make a contended server look like it had more capacity.
  /tiny pure reply, to measure the floor without either workload
"""
import time

from flask import Flask, jsonify

app = Flask(__name__)

# calibrated on this host (calib.py: 19.7M iters/s) so one call is ~10.1 ms of
# CPU time alone; the doc states the measured value, not this constant
CPU_ITERS = 200_000


def burn(n):
    x = 0
    for i in range(n):
        x += i * i
    return x


@app.get("/tiny")
def tiny():
    return jsonify(ok=True)


@app.get("/io")
def io():
    time.sleep(0.020)
    return jsonify(ok=True, slept_ms=20)


@app.get("/cpu")
def cpu():
    t0 = time.process_time()
    x = burn(CPU_ITERS)
    return jsonify(ok=True, x=x & 1, cpu_ms=round((time.process_time() - t0) * 1000, 2))
