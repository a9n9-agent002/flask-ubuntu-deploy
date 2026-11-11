"""Binary check: with --preload, does SIGHUP load new code? And does a restart?

Prints the version each request actually returned, so the result is read off
the server's own answer rather than inferred from timing.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8621


def get(path="/version"):
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as s:
        s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        raw = b""
        while True:
            b = s.recv(4096)
            if not b:
                break
            raw += b
    body = raw.partition(b"\r\n\r\n")[2]
    return json.loads(body)["version"]


def set_version(v):
    path = os.path.join(HERE, "app161.py")
    src = open(path).read().splitlines()
    out = [f'VERSION = "{v}"' if line.startswith("VERSION =") else line for line in src]
    open(path, "w").write("\n".join(out) + "\n")


def start(preload):
    args = [sys.executable, "-m", "gunicorn", "-w", "4", "--timeout", "60", "--log-level", "error"]
    if preload:
        args.insert(3, "--preload")
    args += ["--bind", f"127.0.0.1:{PORT}", "app161:app"]
    return subprocess.Popen(args, cwd=HERE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def ready(timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return get()
        except OSError:
            time.sleep(0.1)
    raise SystemExit("server never answered")


def kill(p):
    try:
        os.kill(p.pid, signal.SIGTERM)
        p.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.kill(p.pid, signal.SIGKILL)
        except OSError:
            pass


def main():
    out = {}
    for preload in (False, True):
        set_version("v1")
        p = start(preload)
        before = ready()
        set_version("v2")
        os.kill(p.pid, signal.SIGHUP)
        time.sleep(2.5)
        after_hup = get()
        # now a full restart with the same preload flag
        kill(p)
        p = start(preload)
        after_restart = ready()
        kill(p)
        out[f"preload={preload}"] = {"before": before, "after_hup": after_hup,
                                    "after_restart": after_restart}
        print(f"--preload={preload}: before={before} after_SIGHUP={after_hup} "
              f"after_restart={after_restart}", flush=True)
    set_version("v1")
    json.dump(out, open(os.path.join(HERE, "preload_check.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
