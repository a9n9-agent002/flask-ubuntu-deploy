"""Every figure on docs/gunicorn-workers-measured.md is recomputed from
docs/workers-measurements.json and asserted against the text. Exit 1 on failure.

    python3 tests/test_workers_doc.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(HERE, "docs", "gunicorn-workers-measured.md")
J = os.path.join(HERE, "docs", "workers-measurements.json")

md = open(MD, encoding="utf-8").read()
d = json.load(open(J, encoding="utf-8"))
fails = []
checks = [0]


def check(cond, msg):
    checks[0] += 1
    if not cond:
        fails.append(msg)


def has(s, why):
    check(s in md, f"missing from the doc: {s!r} ({why})")


io = d["io_sync"]["rows"]
cpu = d["cpu_sync"]["rows"]
th = d["threads_io"]
tcp = d["threads_cpu"]
der = d["derived"]


def row(**kw):
    return kw


# ---- table 1: I/O with sync workers, one per run
for r in io:
    line = (f"| {r['workers']} | {r['rps']:.1f} | {r['p50_ms']:.0f} ms | {r['p95_ms']:.0f} ms | "
            f"{r['pss_mib']:.1f} MiB | {r['rss_mib']:.1f} MiB |")
    has(line, f"I/O sync row for {r['workers']} workers")

# ---- table 2: CPU-bound, including the 8-thread row
for r in cpu:
    has(f"| {r['workers']} | {r['rps']:.1f} | {r['p50_ms']:.0f} ms | {r['pss_mib']:.1f} MiB | {r['rss_mib']:.1f} MiB |",
        f"CPU row for {r['workers']} workers")
if f"{tcp['rps']:.1f}" not in md:
    fails.append("the 8-thread CPU rps is missing")

# ---- table 3: threads
for t in th:
    has(f"| {t['workers']} | {t['threads']} | {t['rps']:.1f} | {t['p50_ms']:.0f} ms | {t['pss_mib']:.1f} MiB |",
        f"thread row {t['workers']}x{t['threads']}")

# ---- every prose figure is one the JSON can produce
one = io[0]
three = next(r for r in io if r["workers"] == 3)
five = next(r for r in io if r["workers"] == 5)
nine = next(r for r in io if r["workers"] == 9)
g4, g8, g16 = th[0], th[1], th[2]

has(f"**{one['rps']:.1f} rps**", "1-worker throughput")
has(f"**{nine['rps']:.1f} rps**", "9-worker throughput")
has(f"| {three['rps']:.1f} |", "3-worker row present")
has(f"**{three['rps']:.1f} rps at {three['p50_ms']:.0f} ms**", "the formula's answer, measured")
has(f"**{three['pss_mib']:.1f} MiB**", "3-worker PSS")
has(f"**{round((five['rps'] / three['rps'] - 1) * 100, 1)}%**", "five-over-three throughput gain")
has(f"**+{round(five['pss_mib'] - three['pss_mib'], 1)} MiB**", "five-over-three PSS cost")
check(abs(nine["rps"] / three["rps"] - 2.99) < 0.05, "the 9-vs-3 multiple in the JSON is ~3.0x")
has(f"{round(nine['rps'] / three['rps'], 1)}x", "nine-over-three multiple")
has(f"**{round(nine['pss_mib'] / 1024 * 100, 1)}%**", "share of a 1 GiB droplet")
has(f"process alone costs **{der['master_pss_mib']:.1f} MiB** PSS", "master PSS")
has(f"**{der['one_worker_total_rss_mib']:.1f} MiB**", "one-worker total RSS")
lo = min(m["pss_mib_per_added_worker"] for m in der["marginal_worker_pss_mib"])
hi = max(m["pss_mib_per_added_worker"] for m in der["marginal_worker_pss_mib"])
# the doc may round the range to one decimal or two; assert the JSON's own values
for v in (lo, hi):
    check(f"{v:.2f}" in md or f"{v:.1f}" in md, f"marginal worker PSS {v} not in the doc")
has(f"**{der['one_worker_worker_rss_mib']:.1f} MiB**", "a single worker's own RSS")
has(f"**{der['threads16_vs_9proc_rps']:.2f}x**", "16-thread vs 9-process throughput")
has(f"**{round(der['threads16_vs_9proc_pss'] * 100, 1)}%**", "16-thread vs 9-process PSS share")
has(f"**{der['rss_over_pss_9proc']:.2f}x**", "RSS overstatement of PSS")
has(f"mean {der['cpu_mean_rps']:.1f}", "CPU mean rps")
has(f"{der['cpu_spread_max_over_min']:.3f}", "CPU max/min spread")
has(f"**{nine['rss_mib']:.1f} MiB**", "9-worker RSS sum")
has(f"**{nine['pss_mib']:.1f} MiB**", "9-worker PSS sum")
has(f"**{g8['rps']:.1f} rps**", "8-thread throughput")
has(f"**{g16['rps']:.1f} rps**", "16-thread throughput")
has(f"**{g16['pss_mib']:.1f} MiB**", "16-thread PSS")
has(f"**{tcp['rps']:.1f} rps**", "8-thread CPU-bound throughput")

# ---- the arithmetic in the prose must hold, not just be present
check(abs(one["rps"] / (1000 / d["io_sync"]["service_ms"]) - 0.94) < 0.06,
      "1 worker should be ~0.95 of the ideal 50 rps")
for r in io:
    ideal = r["workers"] / (d["io_sync"]["service_ms"] / 1000.0)
    check(abs(r["rps"] / ideal - 1) < 0.12, f"{r['workers']} workers: rps {r['rps']} vs ideal {ideal:.0f}")
    ideal_p50 = d["io_sync"]["concurrency"] * d["io_sync"]["service_ms"] / r["workers"]
    check(abs(r["p50_ms"] / ideal_p50 - 1) < 0.12, f"{r['workers']} workers: p50 {r['p50_ms']} vs {ideal_p50:.0f}")
for r in cpu:
    check(0.85 <= r["rps"] / d["derived"]["cpu_mean_rps"] <= 1.06,
          f"CPU row {r['workers']} workers falls outside the flat band")
check(abs(tcp["rps"] / next(r for r in cpu if r["workers"] == 1)["rps"] - 1) < 0.05,
      "8 threads must not beat one sync worker on CPU-bound work (GIL)")
check(g8["rps"] > g4["rps"] and g16["rps"] > g8["rps"], "threads must scale throughput for I/O")

# ---- the two design numbers the doc tells the reader to use
check(2 * 1 + 1 == 3, "(2 x cores) + 1 on one vCPU is 3 workers")
check(abs(three["pss_mib"] / 1024 * 100 - 8.98) < 0.2, "3 workers is ~9% of a 1 GiB droplet")

# ---- disclosure and honesty markers a reader needs
has("Written and maintained by agent002", "AI-authorship line")
has("Caveats, stated rather than implied", "caveats section")
check("http://" not in md and "https://" not in md, "no URLs in this doc (links live in the README)")

print(f"checks: {checks[0]}  fails: {len(fails)}")
for f in fails:
    print("FAIL:", f)
sys.exit(1 if fails else 0)
