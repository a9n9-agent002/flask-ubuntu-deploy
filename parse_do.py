"""Parse digitalocean.com/pricing/droplets into JSON.

The page renders one HTML table per Droplet family, each under an <h3> ("Basic Droplets",
"CPU-Optimized Droplets", …), so the parser tracks the last heading seen and attaches it to the
table that follows. Columns differ slightly between families (CPU-Optimized and up carry an extra
"SSD Variant" column), so columns are mapped BY HEADER NAME, never by position.

    python3 parse_do.py do_droplets.html do-prices.json
"""
import json, re, sys, datetime
from html.parser import HTMLParser

HEAD = {'h1', 'h2', 'h3', 'h4', 'h5'}
FAMILY = {'basic': 'Basic', 'cpu': 'CPU-Optimized', 'general': 'General Purpose',
          'memory': 'Memory-Optimized', 'storage': 'Storage-Optimized'}


class Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = None
        self._cap = None
        self._htext = []
        self.tables = []          # [{'heading': str, 'rows': [[cells]]}]
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag in HEAD:
            self._cap = tag
            self._htext = []
        elif tag == 'table':
            self._row = None
            self._cell = None
            self.tables.append({'heading': self.heading, 'rows': []})
        elif tag == 'tr' and self.tables:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == self._cap:
            t = re.sub(r'\s+', ' ', ''.join(self._htext)).strip()
            if t:
                self.heading = t
            self._cap = None
        elif tag in ('td', 'th') and self._cell is not None:
            self._row.append(re.sub(r'\s+', ' ', ''.join(self._cell)).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.tables[-1]['rows'].append(self._row)
            self._row = None

    def handle_data(self, d):
        if self._cap:
            self._htext.append(d)
        if self._cell is not None:
            self._cell.append(d)


def num(s):
    m = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)', s or '')
    return float(m.group(1).replace(',', '')) if m else None


def gib(s):
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*(GiB|MiB|TiB)', s or '')
    if not m:
        return None
    v = float(m.group(1).replace(',', ''))
    return round(v / 1024, 3) if m.group(2) == 'MiB' else (round(v * 1024, 1) if m.group(2) == 'TiB' else v)


raw = open(sys.argv[1], encoding='utf-8', errors='replace').read()
d = Doc()
d.feed(raw)

plans = []
for t in d.tables:
    h = (t['heading'] or '').lower()
    fam = next((v for k, v in FAMILY.items() if k in h), None)
    if not fam or not t['rows']:
        continue
    hdr = [c.lower() for c in t['rows'][0]]
    if 'memory' not in hdr or 'vcpu' not in hdr or '$/mo' not in hdr:
        continue

    def col(*names):
        for n in names:
            if n in hdr:
                return hdr.index(n)
        return None

    c_mem, c_vcpu, c_tr, c_disk = col('memory'), col('vcpu'), col('transfer'), col('ssd')
    c_hr, c_mo = col('$/hr'), col('$/mo')
    for r in t['rows'][1:]:
        if len(r) != len(hdr):
            continue
        mo = num(r[c_mo])
        if mo is None:
            continue
        plans.append({
            'family': fam,
            'memory_gib': gib(r[c_mem]),
            'vcpus': int(num(r[c_vcpu]) or 0),
            'transfer_gib': gib(r[c_tr]),
            'disk_gib': gib(r[c_disk]),
            'usd_per_hour': num(r[c_hr]),
            'usd_per_month': mo,
        })

payload = {
    'source': 'https://www.digitalocean.com/pricing/droplets',
    'fetched': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
    'note': 'Droplet list prices, columns mapped by header name. USD per month is the page\'s own figure; '
            'hourly is the page\'s figure, not month/730.',
    'families': sorted({p['family'] for p in plans}),
    'plans': plans,
}
json.dump(payload, open(sys.argv[2], 'w'), indent=1)
print(f'{len(plans)} plans across {len(payload["families"])} families -> {sys.argv[2]}')
for p in plans[:5]:
    print(p)
