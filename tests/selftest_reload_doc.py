"""selftest_reload_doc.py - proves tests/test_reload_doc.py fails when
docs/reload-vs-restart-measured.md and docs/reload-measurements.json disagree.

    python3 tests/selftest_reload_doc.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_NAME = "reload-vs-restart-measured.md"
JSON_NAME = "reload-measurements.json"
CHECK = os.path.join(HERE, "tests", "test_reload_doc.py")

d = json.load(open(os.path.join(HERE, "docs", JSON_NAME), encoding="utf-8"))
doc = open(os.path.join(HERE, "docs", DOC_NAME), encoding="utf-8").read()


def build(tmp, doc_text, json_obj):
    os.makedirs(os.path.join(tmp, "docs"))
    os.makedirs(os.path.join(tmp, "tests"))
    with open(os.path.join(tmp, "docs", DOC_NAME), "w", encoding="utf-8") as f:
        f.write(doc_text)
    with open(os.path.join(tmp, "docs", JSON_NAME), "w", encoding="utf-8") as f:
        json.dump(json_obj, f, indent=1)
    shutil.copy(CHECK, os.path.join(tmp, "tests", "test_reload_doc.py"))


def run(tmp):
    return subprocess.run([sys.executable, os.path.join(tmp, "tests", "test_reload_doc.py")],
                          capture_output=True, text=True)


def bump_preload_versions(j):
    for r in j["repeat"]:
        if r.get("group") == "reload_preload":
            r["ok_versions"] = ["v1", "v2"]
    return j


def bump_reload_drops(j):
    for r in j["repeat"]:
        if r.get("group") == "reload":
            r["failed"] = 3
    return j


def bump_restart_gap(j):
    for r in j["repeat"]:
        if r.get("group") == "restart":
            r["gap_ms"] = 2600
    return j


def bump_preload_baseline(j):
    for r in j["repeat"]:
        if r.get("group") == "reload_preload":
            r["pss_baseline_kib"] = 120000
    return j


def bump_floor(j):
    for r in j["scenarios"]:
        if r["scenario"] == "restart_fast_request":
            r["gap_ms"] = 900
    return j


MUTATIONS = [
    ("the reload drop count is edited in the doc",
     lambda t: t.replace("0 of 45", "2 of 45", 1), lambda j: j),
    ("the reload new-code time is edited in the doc",
     lambda t: t.replace("1.712 s", "1.240 s"), lambda j: j),
    ("the preload saving is edited in the doc",
     lambda t: t.replace("42.1 %", "55.0 %"), lambda j: j),
    ("the startup floor is edited in the doc",
     lambda t: t.replace("343 ms", "120 ms"), lambda j: j),
    ("a reload run is made to drop requests in the data",
     lambda t: t, bump_reload_drops),
    ("the preload reload is made to serve new code in the data",
     lambda t: t, bump_preload_versions),
    ("the restart gap is changed in the data, not the doc",
     lambda t: t, bump_restart_gap),
    ("the preload baseline is raised to remove the saving",
     lambda t: t, bump_preload_baseline),
    ("the startup floor is changed in the data",
     lambda t: t, bump_floor),
    ("the AI-authorship line is removed",
     lambda t: t.replace("Written by agent002, an AI agent.", "Written by a person.", 1), lambda j: j),
    ("the binary preload evidence is deleted from the doc",
     lambda t: t.replace("preload=True:  before=v1 after_SIGHUP=v1 after_restart=v2", "", 1), lambda j: j),
]

bad = []
tmp = tempfile.mkdtemp(prefix="selftest_reload_clean_")
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

for label, mutate_doc, mutate_json in MUTATIONS:
    tmp = tempfile.mkdtemp(prefix="selftest_reload_mut_")
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
print(f"SELFTEST PASS - the doc checker fails on all {len(MUTATIONS)} mutations")
