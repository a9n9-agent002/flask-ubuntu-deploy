"""Load generator: C threads, each doing sequential GETs for T seconds.

Records every request's latency so the caller can compute completed requests,
p50/p95 and the mean. Pure stdlib (http.client) so the client adds as little
Python overhead as possible on a loaded box.
"""
import http.client
import threading
import time


def one_thread(port, path, deadline, out, lock):
    conn = None
    local = []
    try:
        while time.time() < deadline:
            if conn is None:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            t0 = time.perf_counter()
            try:
                conn.request("GET", path)
                r = conn.getresponse()
                r.read()
                ok = r.status == 200
            except Exception:
                ok = False
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            dt = time.perf_counter() - t0
            if ok:
                local.append(dt)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        with lock:
            out.extend(local)


def run(port, path, concurrency, duration_s):
    out = []
    lock = threading.Lock()
    deadline = time.time() + duration_s
    threads = [
        threading.Thread(target=one_thread, args=(port, path, deadline, out, lock))
        for _ in range(concurrency)
    ]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    out.sort()
    n = len(out)
    if n == 0:
        return {"n": 0, "rps": 0.0, "p50_ms": None, "p95_ms": None, "mean_ms": None, "elapsed_s": round(elapsed, 2)}
    def pct(p):
        idx = min(n - 1, int(round(p * (n - 1))))
        return round(out[idx] * 1000, 2)
    return {
        "n": n,
        "rps": round(n / elapsed, 2),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "mean_ms": round(sum(out) / n * 1000, 2),
        "max_ms": round(out[-1] * 1000, 2),
        "elapsed_s": round(elapsed, 2),
    }
