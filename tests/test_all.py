"""Assertions on do-prices.json plus a CLI smoke test. Exit 1 on failure.

    python3 tests/test_all.py
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(HERE, 'do-prices.json')))
plans = d['plans']
fails = []


def check(c, m):
    if not c:
        fails.append(m)


check(d['source'] == 'https://www.digitalocean.com/pricing/droplets', 'source url')
check(len(plans) == 31, f'expected 31 plans (7 Basic + 6 in each of 4 families), got {len(plans)}')
check(len({p['family'] for p in plans}) == 5, 'expected 5 families')
for p in plans:
    for k in ('memory_gib', 'vcpus', 'disk_gib', 'usd_per_hour', 'usd_per_month'):
        check(p.get(k) is not None, f'{p}: missing {k}')
    check(p['usd_per_month'] > 0 and p['usd_per_hour'] > 0, f'{p}: non-positive price')
    # the page's hourly figure must be consistent with its monthly figure to within a month's hours
    ratio = p['usd_per_hour'] * 730 / p['usd_per_month']
    check(0.95 <= ratio <= 1.15, f"{p['family']} {p['memory_gib']}G: hourly*730 = "
                                 f"{p['usd_per_hour'] * 730:.2f} vs monthly {p['usd_per_month']:.2f}")

basic = [p for p in plans if p['family'] == 'Basic']
check(min(p['usd_per_month'] for p in basic) == 4.0, 'the $4 Basic droplet is missing')
check(any(p['memory_gib'] == 1 and p['usd_per_month'] == 6.0 for p in basic), 'the $6 1 GiB Basic is missing')

r = subprocess.run([sys.executable, 'pick.py', '--min-ram', '1', '--min-vcpu', '1', '--json'],
                   cwd=HERE, capture_output=True, text=True)
check(r.returncode == 0, f'pick.py failed: {r.stderr[:200]}')
if r.returncode == 0:
    j = json.loads(r.stdout)
    check(j['results'], 'pick.py --json returned nothing')
    cheapest = j['results'][0]
    check(cheapest['memory_gib'] >= 1 and cheapest['vcpus'] >= 1, 'pick.py returned a plan below the requirement')
    check(cheapest['usd_per_month'] == 6.0, f"expected the $6 plan cheapest, got {cheapest['usd_per_month']}")

r = subprocess.run([sys.executable, 'pick.py', '--min-ram', '0.5', '--hours', '40'],
                   cwd=HERE, capture_output=True, text=True)
check(r.returncode == 0 and '0.24' in r.stdout, 'pick.py --hours did not price 40 h at the hourly rate')

# The README and SIZING.md are allowed to quote the WSGI measurement, but not to disagree with the
# generator that produces SIZING.md: every figure below is derived from the same two constants.
sys.path.insert(0, os.path.join(HERE, 'deploy'))
import render_sizing  # noqa: E402

w2, w4 = render_sizing.WSGI_2W_MB, render_sizing.WSGI_4W_MB
readme = ' '.join(open(os.path.join(HERE, 'README.md')).read().split())
sizing = ' '.join(open(os.path.join(HERE, 'deploy', 'SIZING.md')).read().split())
for name, text in (('README.md', readme), ('deploy/SIZING.md', sizing)):
    check(f'{w2:.0f} MiB across the master' in text, f'{name}: two-worker figure disagrees with render_sizing')
    check(f'{w4:.0f} MiB across the master and four' in text,
          f'{name}: four-worker figure disagrees with render_sizing')
check(f'~{w2 + 300:.0f} MiB' in readme and f'~{w4 + 300:.0f} MiB' in readme,
      'README: total (service + host) figures disagree with render_sizing')
check(f'{(w4 - w2) / 2:.0f} MiB per extra worker' in readme,
      'README: per-worker figure disagrees with render_sizing')
check('used to be the one thing in this repo a reader had to take on faith' in sizing,
      'SIZING.md: the framework paragraph should say the WSGI path is now executed')
check('test_wsgi.sh' in readme and 'test_wsgi.sh' in sizing,
      'both docs should name the test that runs the framework path')

# docs/gunicorn-memory-per-worker.md quotes measured numbers; its own checker recomputes them from
# docs/memory-measurements.json, and its selftest proves the checker fails on a mutated doc.
for script in ('tests/test_docs.py', 'tests/selftest_docs.py', 'tests/test_v5.py',
               'tests/test_service_doc.py', 'tests/selftest_service_doc.py',
               'tests/test_workers_doc.py', 'tests/selftest_workers_doc.py',
               'tests/test_reload_doc.py', 'tests/selftest_reload_doc.py',
               'tests/test_caddy_doc.py', 'tests/selftest_caddy_doc.py'):
    r = subprocess.run([sys.executable, script], cwd=HERE, capture_output=True, text=True)
    check(r.returncode == 0, f'{script} failed: {(r.stdout + r.stderr).strip()[-300:]}')

print(f'checked {len(plans)} plans across {len({p["family"] for p in plans})} families')
if fails:
    print('FAIL')
    for f in fails:
        print(' -', f)
    sys.exit(1)
print('ALL TESTS PASS')
