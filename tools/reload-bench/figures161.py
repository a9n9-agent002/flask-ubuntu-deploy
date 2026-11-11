"""Prints every figure the reload doc quotes, computed from res161.json.

Run it, then write the doc from its output - do not hand-copy.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(HERE, "res161.json")))
by = {r["scenario"]: r for r in rows}


def f(kib):
    return round(kib / 1024.0, 1)


print("scenarios:", sorted(by))
print()
hdr = f"{'scenario':24} {'slow_s':>6} {'gap_ms':>7} {'refuse_ms':>9} {'first_v2_s':>10} "
hdr += f"{'in_flight':>9} {'cut':>4} {'pss_base':>9} {'pss_peak':>9} {'ratio':>6}"
print(hdr)
for name in ("reload", "socket_activation", "restart_fast_request", "restart",
             "restart_slow_request"):
    r = by[name]
    gap = "-" if r["gap_ms"] is None else r["gap_ms"]
    ratio = round(r["pss_peak_kib"] / r["pss_baseline_kib"], 2)
    print(f"{name:24} {r['slow_s']:6} {str(gap):>7} {r['refused_window_ms']:9} "
          f"{r['first_v2_s']:10} {r['spanning_total']:9} {r['spanning_failed']:4} "
          f"{f(r['pss_baseline_kib']):9} {f(r['pss_peak_kib']):9} {ratio:6}")
print()
print("reload peak over baseline (MiB):",
      f(by["reload"]["pss_peak_kib"]), "vs", f(by["reload"]["pss_baseline_kib"]),
      "=", round(by["reload"]["pss_peak_kib"] / by["reload"]["pss_baseline_kib"], 2), "x")
print("reload new-code time (s):", by["reload"]["first_v2_s"],
      "| socket-activation:", by["socket_activation"]["first_v2_s"],
      "| restart 1.5s:", by["restart"]["first_v2_s"])
print()
base = by["restart_fast_request"]["gap_ms"]
print("startup floor (fast requests, gap ms):", base)
for name in ("restart", "restart_slow_request"):
    g = by[name]["gap_ms"]
    print(f"{name}: gap {g} ms = startup {base} + in-flight wait {g - base} ms "
          f"(declared request {by[name]['slow_s']} s)")
print()
print("reload dropped:", by["reload"]["failed"], "of", by["reload"]["requests"], "requests")
print("socket-activation dropped:", by["socket_activation"]["failed"],
      "of", by["socket_activation"]["requests"])
for name in ("restart_fast_request", "restart", "restart_slow_request"):
    print(name, "in-flight at cut:", by[name]["spanning_total"],
          "cut:", by[name]["spanning_failed"],
          "reset errors:", by[name]["errors"].get("ConnectionResetError", 0))
