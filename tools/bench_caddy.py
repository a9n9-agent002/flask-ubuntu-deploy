#!/usr/bin/env python3
"""bench_caddy.py — measure what the Caddy reverse proxy actually costs on a small Droplet.

The page-1 shelf for `caddy memory usage` states 10 MB, 30-50 MB, 300 MB and 1 GB, and no page
says which unit, at what concurrency, with which payload, or how long after the load it read the
number. This harness answers that with four phases over one stack:

    caddy reverse_proxy -> deploy/app/server.py (the stdlib SQLite API in this repo)

  main      idle, then 2,000 requests at concurrency 10, then 50 idle keep-alive connections,
            then 60 s of idle; optionally again with GOMEMLIMIT=64MiB
  soak      20,000 requests at concurrency 20, then PSS at +0 s, +30 s, +120 s
  compress  1 MiB responses at concurrency 10 and 100, with no encoding, gzip, and zstd gzip
  sites     one Caddy process holding 1, 10 and 50 site blocks, all proxying the same app

Memory is read as PSS from /proc/<pid>/smaps_rollup (proportional set size: shared pages divided
between the processes that map them), not RSS, because RSS double counts every page a forked
worker shares and Caddy-vs-something comparisons built on RSS are comparing two different units.

Usage (from the repository root, with tools/caddy present or CADDY=/path/to/caddy):
    python3 tools/bench_caddy.py --phase main
    python3 tools/bench_caddy.py --phase all          # writes docs/caddy-memory.raw.json

Requirements: Python 3.9+, the Caddy binary, and a free loopback port set below.
"""
import argparse, json, os, socket, statistics, subprocess, sys, tempfile, threading, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADDY = os.environ.get("CADDY") or os.path.join(ROOT, "tools", "caddy")
APP = os.path.join(ROOT, "deploy", "app", "server.py")
OUT = os.path.join(ROOT, "docs", "caddy-memory.json")
APPPORT, PROXYPORT = 18080, 18081
SITEPORT = 18100
N_REQUESTS, CONCURRENCY, RUNS = 2000, 10, 2
SOAK_N, SOAK_CONC = 20000, 20


def pss_mib(pid):
    try:
        with open("/proc/%d/smaps_rollup" % pid) as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return 0.0
    return 0.0


def rss_mib(pid):
    try:
        with open("/proc/%d/status" % pid) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return 0.0
    return 0.0


def cpu_seconds(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            parts = f.read().split()
        return (int(parts[13]) + int(parts[14])) / os.sysconf("SC_CLK_TCK")
    except Exception:
        return 0.0


def pids_matching(needle):
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as f:
                cmd = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        if needle in cmd:
            out.append(int(entry))
    return out


def caddy_pids():
    """PIDs whose argv[0] IS the caddy binary (not merely a command line that mentions 'caddy',
    which is how `python3 tools/bench_caddy.py` matched itself in the first version of this file)."""
    target = os.path.realpath(CADDY)
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as f:
                argv0 = f.read().split(b"\0")[0].decode("utf-8", "replace")
        except Exception:
            continue
        if argv0 and os.path.realpath(argv0) == target:
            out.append(int(entry))
    return out


def wait_port(port, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def fetch(url, headers=None, timeout=20):
    h = {"Connection": "close"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read()


def fetch_content_encoding(url):
    """Return the Content-Encoding the proxy actually applied — a compression test that never
    negotiated compression would report the uncompressed number under a gzip label."""
    req = urllib.request.Request(url, headers={"Connection": "close", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
        return r.headers.get("Content-Encoding") or "identity"


class Sampler(threading.Thread):
    """Peak PSS of the proxy while a phase runs."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop_flag = 0.0, False

    def run(self):
        while not self.stop_flag:
            for pid in caddy_pids():
                self.peak = max(self.peak, pss_mib(pid))
            time.sleep(0.04)


def start_stack(tmp, gomemlimit=None):
    env = dict(os.environ)
    env.update({
        "NOTES_PORT": str(APPPORT), "NOTES_DB": os.path.join(tmp, "notes.db"), "NOTES_HOST": "127.0.0.1",
        "XDG_DATA_HOME": os.path.join(tmp, "data"), "XDG_CONFIG_HOME": os.path.join(tmp, "config"), "HOME": tmp,
    })
    app = subprocess.Popen([sys.executable, APP], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(APPPORT):
        raise RuntimeError("app did not listen on %d" % APPPORT)
    caddyfile = os.path.join(tmp, "Caddyfile")
    with open(caddyfile, "w") as f:
        f.write("{\n  admin off\n  auto_https off\n}\n:%d {\n  reverse_proxy 127.0.0.1:%d\n}\n" % (PROXYPORT, APPPORT))
    cenv = dict(env)
    if gomemlimit:
        cenv["GOMEMLIMIT"] = gomemlimit
    proxy = subprocess.Popen([CADDY, "run", "--config", caddyfile, "--adapter", "caddyfile"], env=cenv,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(PROXYPORT):
        raise RuntimeError("proxy did not listen on %d" % PROXYPORT)
    time.sleep(1.5)
    return app, proxy


def stop(*procs):
    for p in procs:
        try:
            p.terminate(); p.wait(timeout=10)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def load(n, conc, path="/notes", port=None, headers=None):
    port = port or PROXYPORT
    url = "http://127.0.0.1:%d%s" % (port, path)
    state, errs, lock = {"i": 0}, [], threading.Lock()

    def worker():
        while True:
            with lock:
                if state["i"] >= n:
                    return
                state["i"] += 1
            try:
                fetch(url, headers=headers)
            except Exception as exc:  # noqa: BLE001
                errs.append(repr(exc))

    t0 = time.time()
    ts = [threading.Thread(target=worker) for _ in range(conc)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return state["i"], time.time() - t0, errs[:3]


def hold_keepalive(conns=50, seconds=20):
    socks = []
    for _ in range(conns):
        s = socket.create_connection(("127.0.0.1", PROXYPORT), timeout=5)
        s.sendall(b"GET /notes HTTP/1.1\r\nHost: localhost\r\n\r\n")
        socks.append(s)
    time.sleep(1.0)
    for s in socks:
        try:
            s.recv(4096)
        except OSError:
            pass
    time.sleep(seconds)
    vals = [pss_mib(p) for p in caddy_pids()]
    for s in socks:
        s.close()
    return max(vals) if vals else 0.0


def phase_main(gomemlimit=None, settle=60):
    tmp = tempfile.mkdtemp(prefix="benchcaddy-")
    app, proxy = start_stack(tmp, gomemlimit)
    try:
        for _ in range(20):
            fetch("http://127.0.0.1:%d/notes" % PROXYPORT)
        time.sleep(2.0)
        idle_pss = max(pss_mib(p) for p in caddy_pids())
        idle_rss = max(rss_mib(p) for p in caddy_pids())
        app_pss = max((pss_mib(p) for p in pids_matching("deploy/app/server.py")), default=0.0)
        cpu0 = sum(cpu_seconds(p) for p in caddy_pids())
        s = Sampler(); s.start()
        count, wall, errs = load(N_REQUESTS, CONCURRENCY)
        s.stop_flag = True; s.join()
        cpu1 = sum(cpu_seconds(p) for p in caddy_pids())
        ka = hold_keepalive()
        time.sleep(2.0)
        after_ka = max(pss_mib(p) for p in caddy_pids())
        time.sleep(settle)
        settled = max(pss_mib(p) for p in caddy_pids())
        app_settled = max((pss_mib(p) for p in pids_matching("deploy/app/server.py")), default=0.0)
        return {
            "gomemlimit": gomemlimit, "requests": count, "errors": errs, "load_wall_s": round(wall, 3),
            "rps": round(count / wall, 1) if wall else None,
            "caddy_idle_pss_mib": round(idle_pss, 2), "caddy_idle_rss_mib": round(idle_rss, 2),
            "caddy_peak_pss_mib": round(s.peak, 2),
            "caddy_after_keepalive_pss_mib": round(ka, 2),
            "caddy_after_keepalive_release_pss_mib": round(after_ka, 2),
            "caddy_settled_pss_mib": round(settled, 2),
            "app_idle_pss_mib": round(app_pss, 2), "app_settled_pss_mib": round(app_settled, 2),
            "caddy_cpu_s": round(cpu1 - cpu0, 3),
            "caddy_cpu_ms_per_request": round((cpu1 - cpu0) / max(count, 1) * 1000, 3),
        }
    finally:
        stop(proxy, app)


def phase_soak():
    tmp = tempfile.mkdtemp(prefix="benchsoak-")
    app, proxy = start_stack(tmp)
    try:
        for _ in range(20):
            fetch("http://127.0.0.1:%d/notes" % PROXYPORT)
        time.sleep(2.0)
        pid = caddy_pids()[0]
        idle = pss_mib(pid)
        cpu0 = cpu_seconds(pid)
        s = Sampler(); s.start()
        count, wall, errs = load(SOAK_N, SOAK_CONC)
        s.stop_flag = True; s.join()
        pid = caddy_pids()[0]
        cpu1 = cpu_seconds(pid)
        at_end = pss_mib(pid)
        time.sleep(30); at_30 = pss_mib(caddy_pids()[0])
        time.sleep(90); at_120 = pss_mib(caddy_pids()[0])
        app_pss = max((pss_mib(p) for p in pids_matching("deploy/app/server.py")), default=0.0)
        return {"requests": count, "concurrency": SOAK_CONC, "errors": errs, "wall_s": round(wall, 2),
                "rps": round(count / wall, 1), "idle_pss_mib": round(idle, 2),
                "peak_pss_mib": round(s.peak, 2), "pss_at_end_mib": round(at_end, 2),
                "pss_30s_after_mib": round(at_30, 2), "pss_120s_after_mib": round(at_120, 2),
                "cpu_s": round(cpu1 - cpu0, 2),
                "cpu_ms_per_request": round((cpu1 - cpu0) / max(count, 1) * 1000, 3),
                "app_pss_mib": round(app_pss, 2)}
    finally:
        stop(proxy, app)


def _blob_backend(tmp):
    d = os.path.join(tmp, "www")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "blob.txt"), "wb") as f:
        f.write((b"the quick brown fox jumps over the lazy dog. " * ((1 << 20) // 45 + 1))[: 1 << 20])
    p = subprocess.Popen([sys.executable, "-m", "http.server", str(APPPORT), "--bind", "127.0.0.1", "--directory", d],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(APPPORT):
        raise RuntimeError("blob backend did not listen")
    return p


def _encode_proxy(tmp, encode):
    cf = os.path.join(tmp, "Caddyfile")
    body = "{\n  admin off\n  auto_https off\n}\n:%d {\n  root * %s\n" % (PROXYPORT, os.path.join(tmp, "www"))
    if encode:
        body += "  encode %s\n" % encode
    body += "  file_server\n}\n"
    open(cf, "w").write(body)
    env = dict(os.environ)
    env.update({"XDG_DATA_HOME": os.path.join(tmp, "data"), "XDG_CONFIG_HOME": os.path.join(tmp, "config"), "HOME": tmp})
    p = subprocess.Popen([CADDY, "run", "--config", cf, "--adapter", "caddyfile"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(PROXYPORT):
        raise RuntimeError("proxy did not listen")
    time.sleep(1.5)
    return p


def phase_compress():
    out = {}
    for encode in (None, "gzip", "zstd gzip"):
        reps = []
        for _ in range(2):
            tmp = tempfile.mkdtemp(prefix="beachenc-")
            back = _blob_backend(tmp)
            proxy = _encode_proxy(tmp, encode)
            try:
                fetch("http://127.0.0.1:%d/blob.txt" % PROXYPORT, headers={"Accept-Encoding": "gzip"})
                encoding = fetch_content_encoding("http://127.0.0.1:%d/blob.txt" % PROXYPORT)
                if encode and encoding == "identity":
                    raise RuntimeError("compression was configured but not negotiated (%s)" % encode)
                time.sleep(2.0)
                pid = caddy_pids()[0]
                idle = pss_mib(pid)
                cpu0 = cpu_seconds(pid)
                s = Sampler(); s.start()
                _, w1, e1 = load(200, 10, "/blob.txt", headers={"Accept-Encoding": "gzip"})
                _, w2, e2 = load(200, 100, "/blob.txt", headers={"Accept-Encoding": "gzip"})
                s.stop_flag = True; s.join()
                cpu1 = cpu_seconds(caddy_pids()[0])
                time.sleep(30)
                settled = pss_mib(caddy_pids()[0])
                reps.append({"encode": encode or "none", "content_encoding": encoding,
                             "idle_pss_mib": round(idle, 2),
                             "peak_pss_mib": round(s.peak, 2), "settled_pss_mib": round(settled, 2),
                             "load_c10_s": round(w1, 2), "rps_c10": round(200 / w1, 1),
                             "load_c100_s": round(w2, 2), "rps_c100": round(200 / w2, 1),
                             "cpu_s_total": round(cpu1 - cpu0, 2),
                             "cpu_ms_per_request": round((cpu1 - cpu0) / 400 * 1000, 2),
                             "errors": (e1 + e2)[:3]})
            finally:
                stop(proxy, back)
        out[encode or "none"] = reps
    return out


def phase_sites():
    out = {}
    for n in (1, 10, 50):
        tmp = tempfile.mkdtemp(prefix="benchsites-")
        app, first = start_stack(tmp)
        stop(first)
        time.sleep(1.0)
        cf = os.path.join(tmp, "Caddyfile")
        lines = ["{", "  admin off", "  auto_https off", "}"]
        for i in range(n):
            lines += ["http://site%d.example:%d {" % (i, SITEPORT), "  reverse_proxy 127.0.0.1:%d" % APPPORT, "}"]
        open(cf, "w").write("\n".join(lines) + "\n")
        env = dict(os.environ)
        env.update({"XDG_DATA_HOME": os.path.join(tmp, "data"), "XDG_CONFIG_HOME": os.path.join(tmp, "config"), "HOME": tmp})
        proxy = subprocess.Popen([CADDY, "run", "--config", cf, "--adapter", "caddyfile"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not wait_port(SITEPORT):
                raise RuntimeError("proxy did not listen on %d" % SITEPORT)
            time.sleep(2.0)
            for i in range(min(n, 10)):
                fetch("http://127.0.0.1:%d/notes" % SITEPORT, headers={"Host": "site%d.example" % i})
            time.sleep(2.0)
            idle = pss_mib(caddy_pids()[0])
            s = Sampler(); s.start()
            count, wall, errs = load(100, 5, "/notes", port=SITEPORT)
            s.stop_flag = True; s.join()
            time.sleep(10)
            out[str(n)] = {"sites": n, "idle_pss_mib": round(idle, 2), "peak_pss_mib": round(s.peak, 2),
                           "settled_pss_mib": round(pss_mib(caddy_pids()[0]), 2),
                           "requests": count, "wall_s": round(wall, 2), "errors": errs[:3]}
        finally:
            stop(proxy, app)
    return out


def derive(raw):
    """Every figure the doc prints, recomputed from the raw rows in `raw`.

    The doc test imports this and re-derives from the shipped JSON, so a number in the prose that
    no longer follows from the measurements fails the build instead of ageing silently.
    """
    import statistics
    med = lambda xs: round(statistics.median(xs), 2)
    cpu_hour = 6.00 / 730.0
    runs, gomem = raw["main_runs"], raw["gomemlimit_runs"]
    f = {}
    f["idle_pss_mib"] = med([r["caddy_idle_pss_mib"] for r in runs])
    f["idle_rss_mib"] = med([r["caddy_idle_rss_mib"] for r in runs])
    f["peak_2k_mib"] = med([r["caddy_peak_pss_mib"] for r in runs])
    f["settled_2k_mib"] = med([r["caddy_settled_pss_mib"] for r in runs])
    f["keepalive_mib"] = med([r["caddy_after_keepalive_pss_mib"] for r in runs])
    f["cpu_ms_per_request"] = med([r["caddy_cpu_ms_per_request"] for r in runs])
    f["gomemlimit_peak_mib"] = med([r["caddy_peak_pss_mib"] for r in gomem])
    f["gomemlimit_settled_mib"] = med([r["caddy_settled_pss_mib"] for r in gomem])
    f["app_idle_mib"] = med([r["app_idle_pss_mib"] for r in runs])
    f["app_settled_mib"] = med([r["app_settled_pss_mib"] for r in runs])
    f["growth_2k_mib"] = round(f["peak_2k_mib"] - f["idle_pss_mib"], 2)
    f["peak_over_idle"] = round(f["peak_2k_mib"] / f["idle_pss_mib"], 2)
    soak = raw["soak"]
    f["soak_requests"] = soak["requests"]
    f["soak_peak_mib"] = soak["peak_pss_mib"]
    f["soak_settled_mib"] = soak["pss_at_end_mib"]
    f["soak_120s_mib"] = soak["pss_120s_after_mib"]
    f["app_soak_mib"] = soak["app_pss_mib"]
    f["growth_soak_mib"] = round(f["soak_settled_mib"] - f["idle_pss_mib"], 2)
    comp = raw["compress"]
    f["compress_none_peak"] = med([r["peak_pss_mib"] for r in comp["none"]])
    f["compress_gzip_peak"] = med([r["peak_pss_mib"] for r in comp["gzip"]])
    f["compress_zstd_peak"] = med([r["peak_pss_mib"] for r in comp["zstd gzip"]])
    f["compress_none_cpu_ms"] = med([r["cpu_ms_per_request"] for r in comp["none"]])
    f["compress_gzip_cpu_ms"] = med([r["cpu_ms_per_request"] for r in comp["gzip"]])
    f["gzip_extra_mib"] = round(f["compress_gzip_peak"] - f["compress_none_peak"], 2)
    f["gzip_over_none"] = round(f["compress_gzip_peak"] / f["compress_none_peak"], 2)
    f["gzip_cpu_ratio"] = round(f["compress_gzip_cpu_ms"] / f["compress_none_cpu_ms"], 2)
    f["gzip_extra_per_inflight_mib"] = round(f["gzip_extra_mib"] / 100.0, 3)
    f["inflight_for_1gib"] = round(1024.0 / f["gzip_extra_per_inflight_mib"])
    f["sites_idle"] = {k: v["idle_pss_mib"] for k, v in raw["sites"].items()}
    f["sites_span_mib"] = round(max(f["sites_idle"].values()) - min(f["sites_idle"].values()), 2)
    f["stack_idle_mib"] = round(f["idle_pss_mib"] + f["app_idle_mib"], 2)
    f["stack_load_mib"] = round(f["settled_2k_mib"] + f["app_settled_mib"], 2)
    f["stack_soak_mib"] = round(f["soak_settled_mib"] + f["app_soak_mib"], 2)
    f["pct_of_512"] = round(f["stack_soak_mib"] / 512.0 * 100.0, 1)
    f["pct_of_1024"] = round(f["stack_soak_mib"] / 1024.0 * 100.0, 1)
    f["cpu_usd_per_hour"] = round(cpu_hour, 6)
    f["usd_per_million_requests"] = round(f["cpu_ms_per_request"] / 1000.0 * 1e6 * cpu_hour / 3600.0, 5)
    f["usd_per_million_requests_gzip"] = round(f["compress_gzip_cpu_ms"] / 1000.0 * 1e6 * cpu_hour / 3600.0, 5)
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["main", "soak", "compress", "sites", "all"])
    ap.add_argument("--merge", action="store_true", help="merge into an existing raw file instead of replacing it")
    ap.add_argument("--out", default=OUT, help="where to write the raw+facts JSON")
    a = ap.parse_args()
    if not os.path.exists(CADDY):
        sys.exit("caddy binary not found at %s (set CADDY=/path/to/caddy)" % CADDY)
    raw = {"caddy_version": subprocess.run([CADDY, "version"], capture_output=True, text=True).stdout.split()[0],
           "measured_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    if a.merge and os.path.exists(a.out):
        raw.update(json.load(open(a.out)))
    if a.phase in ("main", "all"):
        raw["main_runs"] = [phase_main() for _ in range(RUNS)]
        raw["gomemlimit_runs"] = [phase_main(gomemlimit="64MiB", settle=20) for _ in range(RUNS)]
    if a.phase in ("soak", "all"):
        raw["soak"] = phase_soak()
    if a.phase in ("compress", "all"):
        raw["compress"] = phase_compress()
    if a.phase in ("sites", "all"):
        raw["sites"] = phase_sites()
    raw.pop("facts", None)
    if all(k in raw for k in ("main_runs", "gomemlimit_runs", "soak", "compress", "sites")):
        raw["facts"] = derive(raw)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(raw, open(a.out, "w"), indent=1, sort_keys=True)
    print("wrote", a.out, "phases:", [k for k in raw if k.endswith("runs") or k in ("soak", "compress", "sites")])
    if "facts" in raw:
        print(json.dumps(raw["facts"], indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
