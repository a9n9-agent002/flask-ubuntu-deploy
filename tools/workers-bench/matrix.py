"""Orchestrate one gunicorn configuration and measure it.

usage: python3 r157/matrix.py --workers 1,2,3,5,9 --path /io --concurrency 20 \
           --duration 6 --taskset 0 --out r157/res_pin1_io.json

For each worker count: start gunicorn (optional `taskset -c <cpus>`), wait for
readiness, warm it with 20 requests, sample the RSS of the master and every
worker, run the load generator, record results, shut down. Everything written
to the JSON file so the doc's numbers can be recomputed by a checker.
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import load  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def pid_tree_ppids():
    """map pid -> ppid from /proc."""
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                data = f.read().decode("ascii", "replace")
        except OSError:
            continue
        # comm may contain spaces/parens; ppid is the field after the last ')'
        rp = data.rfind(")")
        if rp < 0:
            continue
        rest = data[rp + 2:].split()
        out[int(name)] = int(rest[1])
    return out


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def pss_kb(pid):
    """Proportional set size: shared pages split across sharers. RSS sums overstate
    a multi-process deployment because every worker shares the app's code pages."""
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def allowed_cpus(pid):
    """Prove the pin took effect: read Cpus_allowed_list of the running master."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Cpus_allowed_list:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def sample_memory(master):
    ppids = pid_tree_ppids()
    workers = [p for p, pp in ppids.items() if pp == master]
    master_rss = rss_kb(master) or 0
    worker_rss = [(p, rss_kb(p) or 0) for p in sorted(workers)]
    total = master_rss + sum(r for _, r in worker_rss)
    pss_master = pss_kb(master) or 0
    pss_workers = [(p, pss_kb(p) or 0) for p, _ in worker_rss]
    total_pss = pss_master + sum(v for _, v in pss_workers)
    return {
        "master_rss_kb": master_rss,
        "workers": len(worker_rss),
        "worker_rss_kb": [r for _, r in worker_rss],
        "total_rss_kb": total,
        "total_rss_mib": round(total / 1024.0, 2),
        "total_pss_mib": round(total_pss / 1024.0, 2),
        "worker_pss_kb": [v for _, v in pss_workers],
    }


def wait_ready(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def one_run(workers, path, concurrency, duration, taskset, port, worker_class="", threads=0):
    # access logging is off on purpose: it perturbs p50 at this scale
    cmd = [sys.executable, "-m", "gunicorn", "-w", str(workers), "--bind", f"127.0.0.1:{port}",
           "--log-level", "error", "--access-logfile", "/dev/null", "--error-logfile", "/dev/null"]
    if worker_class:
        cmd += ["--worker-class", worker_class]
    if threads:
        cmd += ["--threads", str(threads)]
    cmd += ["app:app"]
    if taskset:
        cmd = ["taskset", "-c", taskset] + cmd
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            return {"workers": workers, "error": "server did not become ready"}
        # warm-up: touch every worker so copy-on-write pages are actually shared/private
        load.run(port, path, min(workers, concurrency), 1.0)
        time.sleep(0.5)
        mem = sample_memory(proc.pid)
        res = load.run(port, path, concurrency, duration)
        res.update(mem)
        res["workers"] = workers
        res["path"] = path
        res["concurrency"] = concurrency
        res["taskset"] = taskset or "none"
        res["worker_class"] = worker_class or "sync"
        res["threads"] = threads or 0
        res["cpus_allowed"] = allowed_cpus(proc.pid)
        return res
    finally:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", default="1,2,3,5,9")
    ap.add_argument("--path", default="/io")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--duration", type=float, default=6.0)
    ap.add_argument("--taskset", default="")
    ap.add_argument("--worker-class", default="")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    results = []
    for w in [int(x) for x in a.workers.split(",")]:
        port = free_port()
        r = one_run(w, a.path, a.concurrency, a.duration, a.taskset, port,
                    worker_class=a.worker_class, threads=a.threads)
        results.append(r)
        print(json.dumps(r), flush=True)
    with open(a.out, "w") as f:
        json.dump({"path": a.path, "concurrency": a.concurrency, "duration_s": a.duration,
                   "taskset": a.taskset or "none", "runs": results}, f, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
