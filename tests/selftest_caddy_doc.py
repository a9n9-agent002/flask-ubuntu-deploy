"""Prove tests/test_caddy_doc.py has teeth: six mutations of the doc or its data must each fail
the checker, and the untouched copy must still pass.

    python3 tests/selftest_caddy_doc.py
"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(HERE, "tests", "test_caddy_doc.py")
SRC_DOC = os.path.join(HERE, "docs", "caddy-memory-usage.md")
SRC_DATA = os.path.join(HERE, "docs", "caddy-memory.json")
problems = []


def run(mutate):
    tmp = tempfile.mkdtemp(prefix="caddyselftest-")
    doc_path = os.path.join(tmp, "caddy-memory-usage.md")
    data_path = os.path.join(tmp, "caddy-memory.json")
    shutil.copy(SRC_DOC, doc_path)
    shutil.copy(SRC_DATA, data_path)
    mutate(doc_path, data_path)
    env = dict(os.environ, CADDY_DOC_DIR=tmp)
    r = subprocess.run([sys.executable, CHECKER], env=env, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def mutate_doc(old, new):
    def f(doc_path, _data):
        text = open(doc_path).read()
        if old not in text:
            raise AssertionError("mutation target %r not in the doc" % old)
        open(doc_path, "w").write(text.replace(old, new, 1))
    return f


def mutate_data(fn):
    def f(_doc, data_path):
        d = json.load(open(data_path))
        fn(d)
        json.dump(d, open(data_path, "w"), indent=1)
    return f


cases = [
    ("a fact that no longer follows from the rows",
     mutate_data(lambda d: d["facts"].__setitem__("idle_pss_mib", 39.0))),
    ("a headline figure edited in the prose",
     mutate_doc("47.50 MiB peak", "47.90 MiB peak")),
    ("a compressed row relabelled as identity",
     mutate_data(lambda d: [r.__setitem__("content_encoding", "identity") for r in d["compress"]["gzip"]])),
    ("the method paragraph removed",
     mutate_doc("smaps_rollup", "the process list")),
    ("a live URL added to a link-free page",
     mutate_doc("## Reproduce", "See https://example.com for more. ## Reproduce")),
    ("the measured idle spread restated wrongly",
     mutate_doc("between 41.54 and 42.12 MiB", "between 40.00 and 44.00 MiB")),
]

for label, fn in cases:
    try:
        rc, out = run(fn)
    except AssertionError as exc:
        problems.append(f"{label}: mutation could not be applied ({exc})")
        continue
    if rc == 0:
        problems.append(f"{label}: the checker PASSED a mutated artifact")
    else:
        print(f"caught: {label}")

rc, out = run(lambda *_: None)
if rc != 0:
    problems.append(f"the untouched copy fails the checker: {out[-300:]}")

if problems:
    print("FAIL")
    for p in problems:
        print(" -", p)
    sys.exit(1)
print(f"{len(cases)}/{len(cases)} mutations caught, control passes")
