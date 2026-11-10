"""selftest_docs.py — proves tests/test_docs.py fails when the doc and the measurements disagree.

A checker nobody has watched fail is not a checker. This copies the doc + data + checker into a temp
tree, applies one mutation at a time, and asserts the checker exits non-zero. Also asserts the clean
copy passes.

    python3 tests/selftest_docs.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(HERE, 'docs', 'gunicorn-memory-per-worker.md')
DATA = os.path.join(HERE, 'docs', 'memory-measurements.json')
CHECK = os.path.join(HERE, 'tests', 'test_docs.py')

MUTATIONS = [
    ('the doc quotes a median it no longer measures', '175 MiB', '171 MiB'),
    ('the marginal-PSS figure is off by one', '21.0 MiB', '20.0 MiB'),
    ('the warming caveat is deleted', 'workers \u00d7 4', 'all the workers'),
    ('the plan arithmetic is dropped', '$6.52', '$6.00'),
]


def run_case(label, old, new):
    tmp = tempfile.mkdtemp(prefix='selftest_docs_')
    try:
        os.makedirs(os.path.join(tmp, 'docs'))
        os.makedirs(os.path.join(tmp, 'tests'))
        shutil.copy(DATA, os.path.join(tmp, 'docs', 'memory-measurements.json'))
        text = open(DOC).read()
        if old not in text:
            return False, f'{label}: mutation target not found in the doc: {old!r}'
        open(os.path.join(tmp, 'docs', 'gunicorn-memory-per-worker.md'), 'w').write(text.replace(old, new, 1))
        shutil.copy(CHECK, os.path.join(tmp, 'tests', 'test_docs.py'))
        r = subprocess.run([sys.executable, os.path.join(tmp, 'tests', 'test_docs.py')],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return False, f'{label}: checker PASSED on a mutated doc (it cannot see this defect)'
        return True, f'{label}: checker failed as it should ({r.stdout.strip().splitlines()[-1][:70]})'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# clean copy must pass
tmp = tempfile.mkdtemp(prefix='selftest_docs_clean_')
try:
    os.makedirs(os.path.join(tmp, 'docs'))
    os.makedirs(os.path.join(tmp, 'tests'))
    shutil.copy(DATA, os.path.join(tmp, 'docs', 'memory-measurements.json'))
    shutil.copy(DOC, os.path.join(tmp, 'docs', 'gunicorn-memory-per-worker.md'))
    shutil.copy(CHECK, os.path.join(tmp, 'tests', 'test_docs.py'))
    r = subprocess.run([sys.executable, os.path.join(tmp, 'tests', 'test_docs.py')],
                       capture_output=True, text=True)
    clean_ok = r.returncode == 0
finally:
    shutil.rmtree(tmp, ignore_errors=True)

bad = [] if clean_ok else ['clean copy did not pass']
print(('ok   ' if clean_ok else 'FAIL ') + 'clean copy passes the doc checker')
for label, old, new in MUTATIONS:
    ok, msg = run_case(label, old, new)
    print(('ok   ' if ok else 'FAIL ') + msg)
    if not ok:
        bad.append(label)

if bad:
    print('FAIL')
    sys.exit(1)
print('SELFTEST PASS — the doc checker fails on all %d mutations' % len(MUTATIONS))
