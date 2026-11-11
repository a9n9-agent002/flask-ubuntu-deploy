"""r161_suggest.py - demand half for the reload/restart doc (control required)."""
import json
import urllib.parse
import urllib.request

HOSTS = [
    'https://suggestqueries.google.com/complete/search?client=firefox&q=',
    'https://www.google.com/complete/search?client=firefox&q=',
]
UA = 'Mozilla/5.0 (compatible; agent002-research/1.0)'

PREFIXES = [
    'systemctl restart',
    'systemctl reload',
    'gunicorn reload',
    'gunicorn graceful',
    'zero downtime deploy',
    'deploy without downtime',
    'systemd socket activation',
    'flask gunicorn systemd',
    'gunicorn zero downtime',
    'restart flask app',
    'systemd restart drops',
]


def suggest(q, host):
    req = urllib.request.Request(host + urllib.parse.quote(q), headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return list(json.loads(r.read().decode('utf-8', 'replace'))[1])
    except Exception as exc:  # noqa: BLE001
        return ['<ERR %r>' % exc]


def main():
    control = suggest('text to speech python', HOSTS[0])
    print('CONTROL -> %d %s' % (len(control), control[:4]))
    if not control:
        print('!!! CONTROL EMPTY - instrument failure')
        return 2
    out = {}
    for p in PREFIXES:
        a = suggest(p, HOSTS[0])
        b = suggest(p, HOSTS[1])
        out[p] = {'google': a, 'google2': b, 'n': len(a)}
        print(f'\n{p!r}: {len(a)} completions')
        for s in a:
            print('   -', s)
    with open('/opt/data/workspace/r161/suggest161.json', 'w') as f:
        json.dump(out, f, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
