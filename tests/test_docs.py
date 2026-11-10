"""Assertions on docs/gunicorn-memory-per-worker.md: every figure it quotes must be derivable from
docs/memory-measurements.json, and the medians must be recomputed here rather than trusted.

    python3 tests/test_docs.py
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
doc = ' '.join(open(os.path.join(HERE, 'docs', 'gunicorn-memory-per-worker.md')).read().split())
data = json.load(open(os.path.join(HERE, 'docs', 'memory-measurements.json')))
runs = data['runs']
fails = []


def check(c, m):
    if not c:
        fails.append(m)


def med(workers, preload, field):
    vals = [r[field] for r in runs if r['workers'] == workers and r['preload'] == preload]
    check(len(vals) >= 3, f'need >=3 runs for {workers}w preload={preload}, got {len(vals)}')
    return st.median(vals), vals


check(len(runs) == 12, f'expected 12 measured runs, got {len(runs)}')
check(data['host_overhead_assumption_mib'] == 300, 'host overhead assumption changed; the doc quotes 300 MiB')

table = {}
for w in (2, 4):
    for pre in (0, 1):
        for f in ('total_rss_mib', 'total_pss_mib', 'workers_rss_mib'):
            table[(w, pre, f)] = med(w, pre, f)

# Every cell of the doc's first table, recomputed from the data and looked for in the prose.
for (w, pre, f), (m, vals) in table.items():
    check(f'{m:.0f} MiB' in doc,
          f'doc does not quote the recomputed {w}w preload={pre} {f} median ({m:.0f} MiB)')

rss2 = table[(2, 0, 'total_rss_mib')][0]
rss4 = table[(4, 0, 'total_rss_mib')][0]
pss2 = {p: table[(2, p, 'total_pss_mib')][0] for p in (0, 1)}
pss4 = {p: table[(4, p, 'total_pss_mib')][0] for p in (0, 1)}

# The spread the doc quotes as a range must be the spread actually observed.
check(min(table[(2, 0, 'total_rss_mib')][1]) == 101, 'low end of the 2-worker RSS range is not 101 MiB')
check(max(table[(2, 0, 'total_rss_mib')][1]) == 104, 'high end of the 2-worker RSS range is not 104 MiB')
check(min(table[(4, 0, 'total_rss_mib')][1]) == 172, 'low end of the 4-worker RSS range is not 172 MiB')
check(max(table[(4, 0, 'total_rss_mib')][1]) == 178, 'high end of the 4-worker RSS range is not 178 MiB')

# Derived claims.
marg = {p: (pss4[p] - pss2[p]) / 2 for p in (0, 1)}
check(f'{marg[0]:.1f} MiB' in doc, f"marginal PSS/worker without preload ({marg[0]:.1f}) not in doc")
check(f'{marg[1]:.1f} MiB' in doc, f"marginal PSS/worker with preload ({marg[1]:.1f}) not in doc")
ratio = marg[0] / marg[1]
check(f'{ratio:.2f}\u00d7' in doc, f'preload ratio ({ratio:.2f}x) not in doc')
save2, save4 = pss2[0] - pss2[1], pss4[0] - pss4[1]
check(f'{save2:.0f} MiB' in doc and f'{save4:.0f} MiB' in doc, 'PSS savings (16/41 MiB) not in doc')
check(f'{save2 / pss2[0] * 100:.1f}%' in doc, 'the 22.9% saving is not in doc')
check(f'{save4 / pss4[0] * 100:.1f}%' in doc, 'the 36.6% saving is not in doc')

# Marginal RSS per worker, which is the number a ps-based guide would quote.
marg_rss = (rss4 - rss2) / 2
check(f'{marg_rss:.1f} MiB' in doc, f'marginal RSS/worker ({marg_rss:.1f}) not in doc')
for w in (2, 4):
    delta = table[(w, 1, 'total_rss_mib')][0] - table[(w, 0, 'total_rss_mib')][0]
    check(f'+{delta:.0f} MiB' in doc, f'preload RSS delta at {w} workers (+{delta:.0f}) not in doc')

# Plan arithmetic: totals with the stated host overhead, and which plan they fit.
host = data['host_overhead_assumption_mib']
t2, t4 = rss2 + host, rss4 + host
check(f'~{t2:.0f} MiB' in doc, f'2-worker total (~{t2:.0f} MiB) not in doc')
check(f'~{t4:.0f} MiB' in doc, f'4-worker total (~{t4:.0f} MiB) not in doc')
check(f'{512 - t2:.0f} MiB left' in doc, f'headroom on 512 MiB for 2 workers ({512 - t2:.0f}) not in doc')
check(f'{512 - t4:.0f} MiB left' in doc, f'headroom on 512 MiB for 4 workers ({512 - t4:.0f}) not in doc')
check(f'{1024 - t4:.0f} MiB left' in doc, f'headroom on 1 GiB for 4 workers ({1024 - t4:.0f}) not in doc')
check('$6.52' in doc and '671.9 h' in doc, 'the $6.52 / 671.9 h arithmetic is missing')

# The measurement caveats that make the numbers reusable must stay in the page.
check('workers \u00d7 4' in doc or 'workers x 4' in doc, 'the warming procedure is not stated')
check('122\u2013163 MiB' in doc, 'the unwarmed first-pass spread is not stated')
check('PSS' in doc and 'RSS' in doc, 'both counting methods must be explained')
check('Not measured here' in doc, 'the page must say what it did not measure')
check('agent002, an autonomous AI agent' in doc, 'AI-authorship disclosure missing')
check('affiliate link' in doc and 'm.do.co' in doc, 'the affiliate link must be labelled in the same page')

print(f'recomputed {len(table)} medians from {len(runs)} measured runs')
if fails:
    print('FAIL')
    for f in fails:
        print(' -', f)
    sys.exit(1)
print('ALL DOC CHECKS PASS')
