"""Prove tests/test_service_doc.py can fail. A guard that passes on its first run may simply be
unable to see the defect (this repository has produced six of those), so each mutation below edits a
copy of the document and the checked script must exit non-zero on it.

    python3 tests/selftest_service_doc.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(HERE, 'docs', 'gunicorn-service-file.md')
original = open(DOC).read()

MUTATIONS = [
    ('a measured total drifts by 1 MiB', '**175 MiB**', '**174 MiB**'),
    ('the unit block weakens the cap', 'MemoryMax=256M', 'MemoryMax=1024M'),
    ('the honesty section disappears', 'no part of this suite runs systemd', 'this suite runs systemd'),
    ('the marginal worker figure is invented', '**35.5 MiB**', '**45.5 MiB**'),
    ('the page-one gap sentence inverts', 'None of the six sets', 'All six set'),
]


def run(doc_text):
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False) as fh:
        fh.write(doc_text)
        path = fh.name
    env = dict(os.environ, SERVICE_DOC=path)
    try:
        return subprocess.run([sys.executable, os.path.join(HERE, 'tests', 'test_service_doc.py')],
                              env=env, capture_output=True, text=True).returncode
    finally:
        os.unlink(path)


fails = 0
# Control: the unmodified document must pass, otherwise the mutations prove nothing.
if run(original) != 0:
    print('FAIL control: the unmodified doc does not pass its own checker')
    sys.exit(1)

for name, old, new in MUTATIONS:
    if old not in original:
        print(f'FAIL mutation target not present: {old!r} ({name})')
        fails += 1
        continue
    code = run(original.replace(old, new, 1))
    if code == 0:
        print(f'FAIL mutation not caught: {name}')
        fails += 1
    else:
        print(f'  ok   caught: {name}')

print('ALL MUTATIONS CAUGHT' if not fails else f'{fails} MUTATION(S) MISSED')
sys.exit(1 if fails else 0)
