"""Every figure in docs/reload-vs-restart-measured.md must be recomputable from
docs/reload-measurements.json.

    python3 tests/test_reload_doc.py
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
doc = ' '.join(open(os.path.join(HERE, 'docs', 'reload-vs-restart-measured.md')).read().split())
data = json.load(open(os.path.join(HERE, 'docs', 'reload-measurements.json')))
runs = data['repeat']
scen = {r['scenario']: r for r in data['scenarios']}
pre = data['preload_check']
fails = []


def check(c, m):
    if not c:
        fails.append(m)


def group(g):
    rows = [r for r in runs if r.get('group') == g]
    check(len(rows) == 3, f'need 3 runs for {g}, got {len(rows)}')
    return rows


def mi(kib):
    return round(kib / 1024.0, 1)


def thousands(n):
    return f'{n:,}'


# ---------------------------------------------------------------- reload ----
r = group('reload')
v2 = [x['first_v2_s'] for x in r]
med = round(st.median(v2), 3)
check(med == 1.712, f'reload first_v2 median is {med}, doc says 1.712')
check(f'{med} s' in doc, f'doc does not quote reload new-code time {med} s')
check(f'{min(v2)}–{max(v2)} s' in doc, 'doc does not quote the reload new-code range')
check(all(x['failed'] == 0 for x in r), 'a reload run dropped requests')
check(all(x['requests'] == 45 for x in r), 'reload request counts changed')
check(all(x['spanning_failed'] == 0 for x in r), 'a reload run cut an in-flight request')
check(f'0 of {r[0]["requests"]}' in doc, 'doc does not quote the reload drop count')
base = mi(st.median([x['pss_baseline_kib'] for x in r]))
peak = round(st.median([mi(x['pss_peak_kib']) for x in r]), 1)
ratio = round(peak / base, 2)
check(base == 113.8, f'reload baseline PSS median is {base}, doc says 113.8')
check(f'{base} MiB' in doc, f'doc does not quote reload baseline {base} MiB')
check(f'{peak} MiB' in doc, f'doc does not quote reload peak {peak} MiB')
check(f'{ratio}×' in doc, f'doc does not quote reload peak ratio {ratio}×')
check(all(sorted(x['ok_versions']) == ['v1', 'v2'] for x in r),
      'reload without preload should serve both versions')

# --------------------------------------------------------------- restart ----
r = group('restart')
gaps = [x['gap_ms'] for x in r]
ref = [x['refused_window_ms'] for x in r]
check(round(st.median(gaps)) == 1735, f'restart gap median {st.median(gaps)}')
check(f'{thousands(round(st.median(gaps)))} ms' in doc, 'doc does not quote restart gap median')
check(round(min(gaps)) == 1735 and round(max(gaps)) == 1754,
      f'restart gap range {min(gaps)}-{max(gaps)}')
check(f'{thousands(round(min(gaps)))}–{thousands(round(max(gaps)))} ms' in doc,
      'doc does not quote the restart gap range')
check(round(st.median(ref)) == 3579, f'restart refusal median {st.median(ref)}')
check(f'{thousands(round(st.median(ref)))} ms' in doc, 'doc does not quote refusal window median')
check(f'{thousands(round(min(ref)))}–{thousands(round(max(ref)))} ms' in doc,
      'doc does not quote the refusal window range')
check(all(x['spanning_failed'] == 4 for x in r), 'restart should cut exactly the in-flight count')
check(all(x['spanning_total'] == 8 for x in r), 'restart in-flight count changed')
check('4 of 8' in doc or '**4**' in doc, 'doc does not quote "4 of 8" cut')
check(all(x['failed'] == 272 and x['requests'] == 309 for x in r),
      'restart attempt counts changed')
check(f'272 of {309}' in doc, 'doc does not quote 272 of 309 attempts refused')
check(round(st.median([x['first_v2_s'] for x in r]), 3) == 3.832, 'restart first_v2 median')
check('3.832 s' in doc, 'doc does not quote restart new-code time 3.832 s')

# ------------------------------------------------ startup floor and wait ----
floor = scen['restart_fast_request']['gap_ms']
check(floor == 343, f'startup floor {floor} ms, doc says 343')
check(f'{floor} ms' in doc, 'doc does not quote the 343 ms startup floor')
for name, slow, gap in (('restart', 1.5, scen['restart']['gap_ms']),
                        ('restart_slow_request', 6.0, scen['restart_slow_request']['gap_ms'])):
    wait = gap - floor
    check(scen[name]['slow_s'] == slow, f'{name} slow request value changed')
    check(f'{thousands(gap)} ms' in doc, f'doc does not quote {name} gap {gap}')
    check(f'{thousands(wait)} ms' in doc, f'doc does not quote {name} in-flight wait {wait}')
check(scen['restart']['gap_ms'] - floor == 1390, 'restart in-flight wait should be 1390 ms')
check(scen['restart_slow_request']['gap_ms'] - floor == 3108,
      'slow restart in-flight wait should be 3108 ms')

# ----------------------------------------------- socket activation row ----
s = scen['socket_activation']
check(s['failed'] == 0 and s['requests'] == 37, 'socket activation run changed')
check(f'0 of {s["requests"]}' in doc, 'doc does not quote socket-activation drop count')
check(f'{thousands(s["gap_ms"])} ms' in doc, 'doc does not quote socket-activation gap')
check(f'{s["first_v2_s"]} s' in doc, 'doc does not quote socket-activation new-code time')
check(round(s['first_v2_s'] / 1.712, 1) == 2.2, 'the 2.2x comparison no longer holds')
check('2.2×' in doc, 'doc does not quote the 2.2x slower figure')

# ------------------------------------------------------- preload trap ----
r = group('reload_preload')
check(all(sorted(x['ok_versions']) == ['v1'] for x in r),
      'a preload reload run served new code - the doc claim is falsified')
check(all(x['failed'] == 0 for x in r), 'preload reload should drop nothing')
base_pre = mi(st.median([x['pss_baseline_kib'] for x in r]))
check(base_pre == 65.9, f'preload baseline {base_pre}, doc says 65.9')
check(f'{base_pre} MiB' in doc, 'doc does not quote preload baseline')
saving_pct = round((1 - base_pre / base) * 100, 1)
saving_mib = round(base - base_pre, 1)
check(saving_pct == 42.1 and f'{saving_pct} %' in doc, f'doc does not quote {saving_pct} % saving')
check(saving_mib == 47.9 and f'{saving_mib} MiB' in doc, f'doc does not quote {saving_mib} MiB saving')
peak_pre = round(st.median([mi(x['pss_peak_kib']) for x in r]), 1)
check(peak_pre == 144.3 and f'{peak_pre} MiB' in doc, 'doc does not quote preload peak PSS')
check(round(peak_pre / base_pre, 2) == 2.19 and '2.19×' in doc, 'doc does not quote 2.19x')
check(all(44 <= x['requests'] <= 46 for x in r), 'preload request counts changed')

# --------------------------------------------------- binary preload test ----
check(pre['preload=False']['after_hup'] == 'v2', 'control: SIGHUP without preload must serve v2')
check(pre['preload=True']['after_hup'] == 'v1', 'preload SIGHUP must serve the old code')
check(pre['preload=True']['after_restart'] == 'v2', 'a restart with preload must serve v2')
check('preload=False: before=v1 after_SIGHUP=v2 after_restart=v2' in doc,
      'doc does not carry the control line verbatim')
check('preload=True: before=v1 after_SIGHUP=v1 after_restart=v2' in doc,
      'doc does not carry the preload line verbatim')

# ------------------------------------------------------------ disclosures ----
check('agent002, an AI agent' in doc, 'missing AI-authorship line')
for bad in ('ref=', 'utm_', 'affi' + 'liate', 'cash' + 'back', 'http://', 'https://'):
    check(bad not in doc, f'doc contains {bad!r} - this repo carries no links by choice')

print(f'recomputed {len(fails)} problems across {len(runs)} runs and {len(scen)} scenarios')
if fails:
    print('FAIL')
    for f in fails:
        print(' -', f)
    sys.exit(1)
print('ALL PASS')
