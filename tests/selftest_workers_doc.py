"""selftest_workers_doc.py — proves tests/test_workers_doc.py fails when
docs/gunicorn-workers-measured.md and docs/workers-measurements.json disagree.

A checker nobody has watched fail is not a checker. Copies the doc, the JSON and
the checker into a temp tree, applies one mutation at a time, asserts the checker
exits non-zero, and asserts the clean copy passes.

    python3 tests/selftest_workers_doc.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_NAME = "gunicorn-workers-measured.md"
JSON_NAME = "workers-measurements.json"
CHECK = os.path.join(HERE, "tests", "test_workers_doc.py")

d = json.load(open(os.path.join(HERE, "docs", JSON_NAME), encoding="utf-8"))
io = d["io_sync"]["rows"]
three = next(r for r in io if r["workers"] == 3)
nine = next(r for r in io if r["workers"] == 9)


def build(tmp, doc_text, json_obj):
    os.makedirs(os.path.join(tmp, "docs"))
    os.makedirs(os.path.join(tmp, "tests"))
    with open(os.path.join(tmp, "docs", DOC_NAME), "w", encoding="utf-8") as f:
        f.write(doc_text)
    with open(os.path.join(tmp, "docs", JSON_NAME), "w", encoding="utf-8") as f:
        json.dump(json_obj, f, indent=1, sort_keys=True)
    shutil.copy(CHECK, os.path.join(tmp, "tests", "test_workers_doc.py"))


def run(tmp):
    return subprocess.run([sys.executable, os.path.join(tmp, "tests", "test_workers_doc.py")],
                          capture_output=True, text=True)


doc = open(os.path.join(HERE, "docs", DOC_NAME), encoding="utf-8").read()

# clean copy must pass
bad = []
tmp = tempfile.mkdtemp(prefix="selftest_workers_clean_")
try:
    build(tmp, doc, d)
    r = run(tmp)
    clean_ok = r.returncode == 0
    detail = (r.stdout or r.stderr).strip().splitlines()[-1][:80] if (r.stdout or r.stderr).strip() else ""
finally:
    shutil.rmtree(tmp, ignore_errors=True)
if clean_ok:
    print("ok   clean copy passes the doc checker")
else:
    print("FAIL clean copy did not pass:", detail)
    bad.append("clean")

# mutations: (label, mutate_doc, mutate_json)
MUTATIONS = [
    ("a table cell is edited",
     lambda t: t.replace(f"{three['rps']:.1f}", f"{three['rps'] + 10:.1f}", 1), lambda j: j),
    ("the RSS-over-PSS overstatement is wrong",
     lambda t: t.replace(f"{d['derived']['rss_over_pss_9proc']:.2f}x", "2.50x", 1), lambda j: j),
    ("the 9-worker PSS is changed in the data, not the doc",
     lambda t: t, lambda j: _bump_pss(j)),
    ("the master-process figure is deleted",
     lambda t: t.replace(f"process alone costs **{d['derived']['master_pss_mib']:.1f} MiB** PSS", "process alone costs little", 1),
     lambda j: j),
    ("the AI-authorship line is removed",
     lambda t: t.replace("Written and maintained by agent002", "Written by a person", 1), lambda j: j),
    ("the CPU table is turned into a claim of scaling",
     lambda t: t.replace(f"| 9 | {d['cpu_sync']['rows'][-1]['rps']:.1f} |",
                         f"| 9 | {d['cpu_sync']['rows'][-1]['rps'] * 3:.1f} |", 1), lambda j: j),
]


def _bump_pss(j):
    for r in j["io_sync"]["rows"]:
        if r["workers"] == 9:
            r["pss_mib"] = round(r["pss_mib"] + 30, 1)
    return j


for label, mutate_doc, mutate_json in MUTATIONS:
    tmp = tempfile.mkdtemp(prefix="selftest_workers_mut_")
    try:
        mutated_doc = mutate_doc(doc)
        mutated_json = mutate_json(json.loads(json.dumps(d)))
        if mutated_doc == doc and mutated_json == d:
            print(f"FAIL {label}: mutation did not change anything")
            bad.append(label)
            continue
        build(tmp, mutated_doc, mutated_json)
        r = run(tmp)
        if r.returncode == 0:
            print(f"FAIL {label}: checker PASSED on the mutant (it cannot see this defect)")
            bad.append(label)
        else:
            tail = (r.stdout or r.stderr).strip().splitlines()[-1][:70]
            print(f"ok   {label}: checker failed as it should ({tail})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if bad:
    print("FAIL")
    sys.exit(1)
print(f"SELFTEST PASS — the doc checker fails on all {len(MUTATIONS)} mutations")
