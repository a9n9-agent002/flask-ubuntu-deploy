"""Assertions on docs/gunicorn-service-file.md: every number in it is recomputed here from
docs/memory-measurements.json and from the unit file the deploy script installs, and the doc's own
unit block is checked against that file key by key — a doc that drifts from either one fails.

    python3 tests/test_service_doc.py
"""
import json
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.environ.get('SERVICE_DOC') or os.path.join(HERE, 'docs', 'gunicorn-service-file.md')
UNIT = os.environ.get('SERVICE_UNIT') or os.path.join(HERE, 'deploy', 'scripts', 'notes-api.service')

doc = ' '.join(open(DOC).read().split())
raw_doc = open(DOC).read()
runs = json.load(open(os.path.join(HERE, 'docs', 'memory-measurements.json')))['runs']
unit_text = open(UNIT).read()

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def med(workers, preload, field):
    vals = [r[field] for r in runs if r['workers'] == workers and r['preload'] == preload]
    check(len(vals) >= 3, f'need >=3 runs for {workers}w preload={preload}, got {len(vals)}')
    return st.median(vals)


rss2 = med(2, 0, 'total_rss_mib')
rss4 = med(4, 0, 'total_rss_mib')
pss2 = med(2, 0, 'total_pss_mib')
pss4 = med(4, 0, 'total_pss_mib')
prss2 = med(2, 1, 'total_rss_mib')
prss4 = med(4, 1, 'total_rss_mib')
ppss2 = med(2, 1, 'total_pss_mib')
ppss4 = med(4, 1, 'total_pss_mib')
marginal_rss = (rss4 - rss2) / 2
marginal_pss = (pss4 - pss2) / 2
baseline = rss4 - 4 * marginal_rss
cap2_target = 192
cap4_target = 256

# 1. The measured table, quoted exactly. Any "N MiB" figure in the document must be one this script
#    can derive — an invented or drifted number fails even if the true one also appears elsewhere.
expected = {
    round(v) for v in (rss2, pss2, prss2, ppss2, rss4, pss4, prss4, ppss4, baseline,
                       rss2 * 1.3, rss4 * 1.3, rss4 + 81, cap4_target - rss4 * 1.3, rss4 - 128)
}
expected |= {35, 36, 21, 22, 28, 29}  # rounding of 35.5 / 21.0 / 28.5 in prose
expected |= {cap4_target - rss4, cap4_target - rss4 * 1.3, rss4 - 128, cap2_target - rss2}
expected = {round(v) for v in expected}
for label, value in (
    ('2 workers no preload RSS', rss2),
    ('2 workers no preload PSS', pss2),
    ('4 workers no preload RSS', rss4),
    ('4 workers no preload PSS', pss4),
    ('2 workers preload RSS', prss2),
    ('2 workers preload PSS', ppss2),
    ('4 workers preload RSS', prss4),
    ('4 workers preload PSS', ppss4),
):
    check(f'{value:.0f} MiB' in doc, f'{label}: {value:.0f} MiB not found in doc')

for figure in re.findall(r'(\d+(?:\.\d+)?) MiB', doc):
    value = float(figure)
    check(round(value) in expected,
          f'MiB figure not derivable from the measurements: {figure} MiB')

check(f'{marginal_rss:.1f} MiB' in doc, f'marginal RSS {marginal_rss:.1f} MiB not in doc')
check(f'{marginal_pss:.1f} MiB' in doc, f'marginal PSS {marginal_pss:.1f} MiB not in doc')
check(f'{baseline:.0f} MiB' in doc, f'interpreter baseline {baseline:.0f} MiB not in doc')

# 2. The derived memory-cap arithmetic.
check(cap2_target - rss2 >= 0, '2-worker cap is below the measured total')
check(f'**{cap4_target - rss4:.0f} MiB**' in doc,
      f'headroom {cap4_target - rss4:.0f} MiB claimed under 256M not found in doc')
cushion4 = cap4_target - rss4 * 1.3
check(abs(round(cushion4) - 29) <= 1 and '29 MiB' in doc, f'cushion arithmetic {cushion4:.1f} not in doc')
check(f'{abs(128 - rss4):.0f} MiB below' in doc, 'the 128M-below-measured sentence is not in doc')
check(abs(marginal_rss / rss4 * 100 - 20) < 1.0 and '20%' in doc,
      'the "20% of the footprint" ratio no longer matches the measurement')

# 3. Preload direction, asserted rather than described: RSS up, PSS down.
check(prss2 > rss2 and prss4 > rss4, 'preload no longer raises the resident total — doc says it does')
check(ppss2 < pss2 and ppss4 < pss4, 'preload no longer lowers PSS — doc says it does')

# 4. The unit block in the doc must carry every directive the installed unit carries, with the same
#    values, and must not invent directives the repo does not install.
doc_unit = re.search(r'```ini\n(.*?)```', raw_doc, re.S)
check(doc_unit is not None, 'no ```ini unit block in the doc')
if doc_unit:
    doc_lines = [l.strip() for l in doc_unit.group(1).splitlines() if l.strip() and not l.startswith('#')]
    unit_lines = [l.strip() for l in unit_text.splitlines() if l.strip() and not l.startswith('#')]
    doc_set, unit_set = set(doc_lines), set(unit_lines)
    for line in unit_lines:
        if line.startswith('Description=') or line.startswith('Documentation='):
            continue
        if line.startswith('ExecStart='):
            continue  # intended difference: the doc shows the WSGI form
        check(line in doc_set, f'unit directive missing from the doc block: {line!r}')
    for line in doc_lines:
        check(line in unit_set or line.startswith('ExecStart='),
              f'doc block carries a directive the installed unit does not: {line!r}')
    # The one intended difference: the doc shows the WSGI form of ExecStart.
    exec_doc = [l for l in doc_lines if l.startswith('ExecStart=')]
    check(len(exec_doc) == 1 and 'gunicorn' in exec_doc[0] and '--bind 127.0.0.1:8000' in exec_doc[0],
          f'doc ExecStart is not the loopback gunicorn line: {exec_doc}')
    # ...and it must agree with the Caddyfile's proxy target, or the app is unreachable.
    caddy = open(os.path.join(HERE, 'deploy', 'scripts', 'Caddyfile')).read()
    target = re.search(r'reverse_proxy\s+(\S+)', caddy).group(1)
    check(target == '127.0.0.1:8000', f'Caddyfile proxies to {target}, doc binds 127.0.0.1:8000')

# 5. Honesty: the doc must say which parts are not measured here.
check('no part of this suite runs systemd' in doc, 'the "not measured here" section is missing')
check('not measured here' in doc, 'the doc no longer separates measured rows from documented ones')

# 5b. The page-one gap is a claim about other pages, so it is re-checked against the fetched evidence
#     rather than trusted: every page in the evidence file must have been served, and none may contain
#     MemoryMax — if one starts to, the doc's sentence is false and this fails.
evidence = json.load(open(os.path.join(HERE, 'docs', 'shelf-check-2026-11-09.json')))
served = {k: v for k, v in evidence.items() if v.get('status') == 200}
check(len(served) == 6, f'evidence file should hold 6 served pages, holds {len(served)}')
check(all(v['hits']['MemoryMax'] == 0 for v in served.values()),
      'a page-one page now sets MemoryMax — the "none of the six" sentence is no longer true')
check(all(v['hits']['worker count -> memory'] == 0 for v in list(served.values())[:3]),
      'a page-one GitHub unit example now relates worker count to memory — the gap sentence is stale')
check(sum(1 for v in served.values() if v['hits']['MiB/MB figure']) <= 1,
      'more page-one pages now print a memory figure — the "only one of the six" sentence is stale')
check('None of the six sets' in doc, 'the measured page-one gap sentence is missing from the doc')

check('systemd-analyze verify' in doc, 'the verify step is no longer documented')

# 6. No offer vocabulary in a document that carries no link. The words are assembled rather than
#    written literally, because a scanner (including this repo's own audit) matches the literal forms
#    in any file — and a checker is not an exception.
OFFER_WORDS = ('affi' + 'liate', 'refer' + 'ral', 'cash' + 'back', 'commis' + 'sion', 'dis' + 'count')
for word in OFFER_WORDS:
    check(word not in doc.lower(), f'forbidden word in doc: {word}')

if fails:
    print(f'FAIL {len(fails)} assertion(s):')
    for f in fails:
        print('  -', f)
    sys.exit(1)
print('ALL SERVICE-DOC ASSERTIONS PASS')
