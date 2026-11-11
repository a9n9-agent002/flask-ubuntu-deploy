"""Assertions on docs/caddy-memory-usage.md: every figure it prints must follow from the raw rows
in docs/caddy-memory.json, re-derived here with the harness's own derive().

Three classes of check:
  1. the shipped `facts` block equals derive(raw) — a hand-edited fact fails;
  2. every number the doc prints next to a unit is either a facts value or a raw row value
     (the sibling gunicorn page's 104/70/112 MiB are the only figures imported from elsewhere);
  3. the method sentences that make the numbers meaningful are present, and the page carries no
     live URL (this document is link-free by choice: it sells nothing).

    python3 tests/test_caddy_doc.py
"""
import importlib.util, json, os, re, statistics, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.environ.get("CADDY_DOC_DIR") or os.path.join(HERE, "docs")
md = " ".join(open(os.path.join(DOCS, "caddy-memory-usage.md")).read().split())
raw = json.load(open(os.path.join(DOCS, "caddy-memory.json")))
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


spec = importlib.util.spec_from_file_location("bench_caddy", os.path.join(HERE, "tools", "bench_caddy.py"))
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

# 1. facts are a derivation, not a transcription
facts = raw["facts"]
check(all(k in raw for k in ("main_runs", "gomemlimit_runs", "soak", "compress", "sites")),
      "caddy-memory.json is missing one of the four phases; the doc quotes all of them")
recomputed = bc.derive(raw)
for k in sorted(set(facts) | set(recomputed)):
    check(facts.get(k) == recomputed.get(k),
          f"facts[{k}] = {facts.get(k)!r} but re-deriving from the rows gives {recomputed.get(k)!r}")

# 2. every unit-bearing number in the prose must exist in the data
allowed = set()
for v in recomputed.values():
    if isinstance(v, (int, float)):
        allowed |= {f"{v:.2f}", f"{v:.1f}", f"{v:.3f}", f"{v:.4f}"}
for r in raw["main_runs"] + raw["gomemlimit_runs"]:
    for v in r.values():
        if isinstance(v, (int, float)):
            allowed |= {f"{v:.2f}", f"{v:.1f}", f"{v:.3f}", f"{v:.4f}"}
allowed |= set()
for row in raw["sites"].values():
    for v in row.values():
        if isinstance(v, (int, float)):
            allowed |= {f"{v:.2f}", f"{v:.1f}"}
for group in raw["compress"].values():
    for row in group:
        for v in row.values():
            if isinstance(v, (int, float)):
                allowed |= {f"{v:.2f}", f"{v:.1f}", f"{v:.3f}", f"{v:.4f}"}
for v in raw["soak"].values():
    if isinstance(v, (int, float)):
        allowed |= {f"{v:.2f}", f"{v:.1f}", f"{v:.3f}", f"{v:.4f}"}
# figures quoted from the sibling page (gunicorn-memory-per-worker.md), not from this dataset
SIBLING = {"104", "70", "112"}
# inputs and context, not results: GOMEMLIMIT=64 MiB, the 512 MiB / 1 GiB plans, 1 MiB payloads,
# the 40 ms sampling interval, and the 10x / 20x / 50x load and config multipliers.
PARAMS = {"MiB": {"64", "512", "1"}, "ms": {"40"}, "×": {"10", "20", "50"}, "%": set()}

for unit in ("MiB", "ms", "×", "%"):
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*" + re.escape(unit))
    for token in pattern.findall(md):
        check(token in allowed or token in SIBLING or token in PARAMS[unit],
              f"the doc prints {token} {unit}, which no measurement in caddy-memory.json produces")

# 3. the strings that carry the argument
for needle in ("PSS", "smaps_rollup", "concurrency", "keep-alive", "GOMEMLIMIT",
               "Content-Encoding", "auto_https", "derive()", "tools/bench_caddy.py"):
    check(needle in md, f"the doc no longer explains {needle!r}")
check(not re.search(r"https?://", md), "the doc must stay link-free (it sells nothing)")
check(raw["caddy_version"].startswith("v2."), "caddy version missing from the data file")
check(f"{recomputed['idle_pss_mib']:.2f} MiB" in md, "the idle PSS figure is not in the prose")
check(f"{recomputed['soak_settled_mib']:.2f} MiB" in md, "the soaked PSS figure is not in the prose")
lo = min(r["caddy_idle_pss_mib"] for r in raw["main_runs"] + raw["gomemlimit_runs"])
hi = max(r["caddy_idle_pss_mib"] for r in raw["main_runs"] + raw["gomemlimit_runs"])
check(f"between {lo:.2f} and {hi:.2f} MiB" in md,
      f"the stated idle spread is not the measured spread ({lo:.2f}-{hi:.2f} MiB)")

# the compression rows must show that compression was actually negotiated
for label in ("gzip", "zstd gzip"):
    enc = {r.get("content_encoding") for r in raw["compress"][label]}
    check(enc == {"gzip"}, f"{label} rows negotiated {enc}, not gzip — the compressed figure is fiction")
check({r.get("content_encoding") for r in raw["compress"]["none"]} == {"identity"},
      "the uncompressed control rows were not served as identity")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print(f"caddy doc OK: {len(md)} chars, facts re-derived from {len(raw['main_runs'])} main + "
      f"{len(raw['gomemlimit_runs'])} GOMEMLIMIT runs, {len(raw['compress'])} encode configs, "
      f"{len(raw['sites'])} site counts")
