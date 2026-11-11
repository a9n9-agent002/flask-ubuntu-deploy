"""Builds docs/reload-measurements.json from the r161 run outputs (idempotent).

    python3 tools/reload-bench/build_measurements.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/opt/data/workspace/r161"
OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "reload-measurements.json")

main = json.load(open(os.path.join(SRC, "res161.json")))
rep = json.load(open(os.path.join(SRC, "res161_repeat.json")))
pre = json.load(open(os.path.join(SRC, "preload_check.json")))

keep_main = ("scenario", "mode", "workers", "loaders", "slow_s", "preload", "run_s",
             "requests", "ok", "failed", "errors", "refused_window_ms",
             "spanning_total", "spanning_ok", "spanning_failed", "gap_ms",
             "old_exit_ms", "first_v2_s", "pss_baseline_kib", "pss_peak_kib",
             "ok_versions")
keep_rep = keep_main + ("group",)

doc = {
    "meta": {
        "measured_at_utc": "2026-11-10T05:49-06:00Z",
        "host": "single container, Linux, Python 3.14.9, gunicorn 26.x, Flask 3.x",
        "workers": 4,
        "worker_class": "sync",
        "loaders": 8,
        "workload": "GET /slow sleeping a fixed number of seconds (an I/O wait, not a CPU burn)",
        "memory_metric": "PSS summed over the master and all its children, from /proc/<pid>/smaps_rollup, sampled every 50 ms",
        "note": "each scenario is one run in `scenarios`; `repeat` holds 3 runs each of reload, restart and reload+preload",
    },
    "scenarios": [{k: r.get(k) for k in keep_main} for r in main],
    "repeat": [{k: r.get(k) for k in keep_rep} for r in rep],
    "preload_check": pre,
}
with open(OUT, "w") as f:
    json.dump(doc, f, indent=1)
print("wrote", OUT, len(doc["scenarios"]), "scenarios,", len(doc["repeat"]), "repeats")
