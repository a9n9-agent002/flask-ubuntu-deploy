"""Repeats the two load-bearing scenarios 3x each, so the doc can quote a median
and a range instead of a single run. Writes res161_repeat.json.
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("run161", os.path.join(HERE, "run161.py"))
run161 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run161)

PLAN = [("reload", "hup", 1.5, False, 8631),
        ("restart", "restart", 1.5, False, 8633),
        ("reload_preload", "hup", 1.5, True, 8635)]
REPS = 3
out = []
for name, mode, slow, preload, base_port in PLAN:
    for i in range(REPS):
        port = base_port + i
        r = run161.scenario(f"{name}_{i}", port, mode, slow, preload)
        keep = {k: r[k] for k in ("scenario", "mode", "slow_s", "preload", "gap_ms",
                                  "refused_window_ms", "first_v2_s", "spanning_total",
                                  "spanning_failed", "pss_baseline_kib", "pss_peak_kib",
                                  "ok_versions", "failed", "requests")}
        keep["group"] = name
        print(json.dumps(keep), flush=True)
        out.append(keep)
json.dump(out, open(os.path.join(HERE, "res161_repeat.json"), "w"), indent=1)
print("wrote res161_repeat.json", len(out), "runs")
