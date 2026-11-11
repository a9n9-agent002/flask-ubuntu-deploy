"""Reload vs restart: what each one does to in-flight requests and peak memory.

Three scenarios, identical load in each:
  A  reload    SIGHUP to the gunicorn master (what `systemctl reload` does with
               ExecReload=/bin/kill -s HUP $MAINPID). Workers are replaced one
               at a time; the listening socket is never closed.
  B  restart   SIGTERM the master, wait for exit, start a new master (what
               `systemctl restart` does on a unit WITHOUT socket activation:
               the listening socket closes with the old process and reopens
               only when the new one binds).
  C  restart+socket activation  keeps ONE listening socket open in the harness
               across the swap (systemd socket activation stands in for this),
               so the port never closes; measures what the gap itself costs.

Load: LOADERS threads issuing /slow?s=1.5 back to back, which is longer than a
reload takes, so every request spans the deploy. Every failure is counted, with
its exception class, so "dropped" is measured and not assumed.

Writes res161.json. Numbers only - the doc renderer computes every figure from
this file.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT_A = 8611
PORT_B = 8612
PORT_C = 8613
WORKERS = 4
SLOW_S = 1.5
LOADERS = 8
RUN_S = 14.0
SWITCH_AT = 3.0
V2_SENTINEL = 20.0  # app161.py is rewritten to VERSION="v2" this many seconds in


# ---------------------------------------------------------------- harness ----
def wait_ready(port, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as s:
                s.sendall(b"GET /version HTTP/1.0\r\n\r\n")
                data = s.recv(4096)
                if b"200" in data:
                    return time.time() - t0
        except OSError:
            pass
        time.sleep(0.05)
    return None


def pss_of(pid):
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def children(pid):
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[1]) == pid:
                out.append(int(d))
        except (OSError, IndexError):
            continue
    return out


class MemSampler(threading.Thread):
    """Samples PSS of the master + all its children every 50 ms."""

    def __init__(self, get_master):
        super().__init__(daemon=True)
        self.get_master = get_master
        self.samples = []
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            m = self.get_master()
            if m:
                total = 0
                n = 0
                for pid in [m] + children(m):
                    v = pss_of(pid)
                    if v is not None:
                        total += v
                        n += 1
                if n:
                    self.samples.append((round(time.time(), 3), n, total))
            time.sleep(0.05)


class Loader(threading.Thread):
    def __init__(self, port, sink, stop_flag, slow_s=None):
        super().__init__(daemon=True)
        self.port = port
        self.sink = sink
        self.stop_flag = stop_flag
        self.slow_s = SLOW_S if slow_s is None else slow_s

    def run(self):
        while not self.stop_flag.is_set():
            t0 = time.time()
            rec = {"t0": round(t0, 3), "ok": False, "status": None, "err": None, "version": None}
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=30) as s:
                    s.sendall(f"GET /slow?s={self.slow_s} HTTP/1.0\r\n\r\n".encode())
                    chunks = []
                    while True:
                        b = s.recv(8192)
                        if not b:
                            break
                        chunks.append(b)
                raw = b"".join(chunks)
                head, _, body = raw.partition(b"\r\n\r\n")
                rec["status"] = int(head.split()[1]) if head else None
                rec["ok"] = rec["status"] == 200
                rec["version"] = json.loads(body).get("version") if body.strip() else None
            except Exception as e:  # noqa: BLE001 - the exception class IS the datum
                rec["err"] = type(e).__name__
                rec["t1"] = round(time.time(), 3)
                self.sink.append(rec)
                time.sleep(0.02)  # do not busy-spin while the port is closed
                continue
            rec["t1"] = round(time.time(), 3)
            self.sink.append(rec)


def start_master(port, tag, keep_sock=None, preload=False):
    env = dict(os.environ)
    args = [sys.executable, "-m", "gunicorn", "-w", str(WORKERS), "--timeout", "60",
            "--log-level", "error", "--access-logfile", "-"]
    if preload:
        args.insert(3, "--preload")
    if keep_sock is not None:
        env["GUNICORN_FD"] = str(keep_sock.fileno())
        args += ["--bind", f"fd://{keep_sock.fileno()}", "app161:app"]
        log = open(os.path.join(HERE, f"server_{tag}.log"), "ab", buffering=0)
        p = subprocess.Popen(args, cwd=HERE, env=env, stdout=log, stderr=log,
                             pass_fds=(keep_sock.fileno(),), start_new_session=True)
    else:
        args += ["--bind", f"127.0.0.1:{port}", "app161:app"]
        log = open(os.path.join(HERE, f"server_{tag}.log"), "ab", buffering=0)
        p = subprocess.Popen(args, cwd=HERE, env=env, stdout=log, stderr=log,
                             start_new_session=True)
    return p


def set_version(v):
    path = os.path.join(HERE, "app161.py")
    with open(path) as f:
        src = f.read()
    out = []
    for line in src.splitlines():
        out.append(f'VERSION = "{v}"' if line.startswith("VERSION =") else line)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def cleanup(p):
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except OSError:
            try:
                p.kill()
            except OSError:
                pass
    for tag in ("a", "b", "c"):
        pass


# -------------------------------------------------------------- scenarios ----
def scenario(name, port, mode, slow_s=SLOW_S, preload=False):
    set_version("v1")
    state = {"master": None}
    sock = None
    if mode == "socket":
        # one listening socket, held by the harness: never closes during the swap
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(2048)
        sock.set_inheritable(True)

    p = start_master(port, name, keep_sock=sock, preload=preload)
    state["master"] = p.pid
    ready = wait_ready(port)
    if ready is None:
        return {"error": "server never became ready", "scenario": name}

    mem = MemSampler(lambda: state["master"])
    mem.start()

    sink = []
    stop_flag = threading.Event()
    loaders = [Loader(port, sink, stop_flag, slow_s) for _ in range(LOADERS)]
    for t in loaders:
        t.start()

    t_start = time.time()
    cut = {"t": None, "gap_ms": None, "first_v2": None, "old_exit_ms": None}
    while time.time() - t_start < RUN_S:
        el = time.time() - t_start
        if el >= SWITCH_AT and cut["t"] is None:
            set_version("v2")
            cut["t"] = round(time.time(), 3)
            if mode == "hup":
                os.kill(p.pid, signal.SIGHUP)
            else:  # restart, with or without a socket that survives the swap
                os.kill(p.pid, signal.SIGTERM)
            t_cut = time.time()
            while time.time() - t_cut < (12 if preload else 40):
                if cut["first_v2"] is None and any(
                        r.get("version") == "v2" and r["ok"] for r in list(sink)):
                    cut["first_v2"] = round(time.time() - t_cut, 3)
                if mode != "hup" and cut["old_exit_ms"] is None and p.poll() is not None:
                    cut["old_exit_ms"] = round((time.time() - t_cut) * 1000)
                    p = start_master(port, name + "2", keep_sock=sock if mode == "socket" else None)
                    state["master"] = p.pid
                    cut["gap_ms"] = round((time.time() - t_cut) * 1000)
                if cut["first_v2"] is not None:
                    break
                time.sleep(0.02)
        time.sleep(0.01)

    stop_flag.set()
    for t in loaders:
        t.join(timeout=slow_s + 8)
    mem.stop_flag = True
    mem.join(timeout=2)

    # tidy: kill whichever master is running
    for master in {state["master"], p.pid}:
        try:
            os.killpg(os.getpgid(master), signal.SIGKILL)
        except OSError:
            pass
    if sock:
        sock.close()
    set_version("v1")

    ok = [r for r in sink if r["ok"]]
    bad = [r for r in sink if not r["ok"]]
    errs = {}
    for r in bad:
        k = r["err"] or f"status_{r['status']}"
        errs[k] = errs.get(k, 0) + 1
    spanning = [r for r in sink if cut["t"] and r["t0"] < cut["t"] < r["t1"]]
    spanning_ok = [r for r in spanning if r["ok"]]
    refused_ts = sorted(r["t0"] for r in bad)
    refused_window_ms = (round((refused_ts[-1] - refused_ts[0]) * 1000)
                         if len(refused_ts) > 1 else 0)
    pss = [s[2] for s in mem.samples] or [0]
    baseline = [s[2] for s in mem.samples[:10]] or [0]
    return {
        "scenario": name,
        "mode": mode,
        "port": port,
        "workers": WORKERS,
        "loaders": LOADERS,
        "slow_s": slow_s,
        "preload": preload,
        "run_s": RUN_S,
        "switch_at_s": SWITCH_AT,
        "ready_s": round(ready, 3),
        "requests": len(sink),
        "ok": len(ok),
        "failed": len(bad),
        "errors": errs,
        "refused_window_ms": refused_window_ms,
        "spanning_total": len(spanning),
        "spanning_ok": len(spanning_ok),
        "spanning_failed": len(spanning) - len(spanning_ok),
        "gap_ms": cut["gap_ms"],
        "old_exit_ms": cut["old_exit_ms"],
        "first_v2_s": cut["first_v2"],
        "pss_baseline_kib": sorted(baseline)[len(baseline) // 2],
        "pss_peak_kib": max(pss),
        "pss_samples": len(pss),
        "ok_versions": sorted({r["version"] for r in ok}),
        "requests_summary": sink,
    }


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    plan = [("reload", PORT_A, "hup"), ("restart", PORT_B, "restart"),
            ("socket_activation", PORT_C, "socket"),
            ("restart_slow_request", 8614, "restart", 6.0),
            ("restart_fast_request", 8615, "restart", 0.05),
            ("reload_preload", 8616, "hup", 1.5, True)]
    out = []
    for entry in plan:
        name, port, mode = entry[:3]
        slow_s = entry[3] if len(entry) > 3 else SLOW_S
        preload = entry[4] if len(entry) > 4 else False
        if only and only != name:
            continue
        print(f"--- {name} ---", flush=True)
        r = scenario(name, port, mode, slow_s, preload)
        keep = {k: v for k, v in r.items() if k != "requests_summary"}
        print(json.dumps(keep, indent=1), flush=True)
        out.append(r)
    path = os.path.join(HERE, "res161.json")
    if only:
        prev = []
        if os.path.exists(path):
            prev = json.load(open(path))
        merged = {r["scenario"]: r for r in prev}
        for r in out:
            merged[r["scenario"]] = r
        out = list(merged.values())
    with open(path, "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
