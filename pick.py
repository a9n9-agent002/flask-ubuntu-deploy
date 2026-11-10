#!/usr/bin/env python3
"""Pick the cheapest DigitalOcean Droplet that meets a requirement, and price the extras honestly.

    python3 pick.py --min-ram 1 --min-vcpu 1                 # cheapest plan with >= 1 GiB and >= 1 vCPU
    python3 pick.py --min-ram 2 --min-disk 50 --top 3
    python3 pick.py --min-ram 0.5 --hours 40                 # short-lived: hourly price, not monthly
    python3 pick.py --min-ram 2 --count 3 --backups daily    # three droplets + daily backups
    python3 pick.py --min-ram 1 --json

Prices are the page's own list prices (do-prices.json, dated). "Monthly" is the vendor's $/mo figure;
"730 h" is hourly x 730, which is what you actually pay for a droplet left running — the two differ,
and which is cheaper is arithmetic, not a guess.
"""
import argparse, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = {'weekly': 0.20, 'daily': 0.30}   # DigitalOcean: 20% weekly / 30% daily of droplet cost
HOURS_PER_MONTH = 730


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-ram', type=float, default=0.0, help='GiB of RAM required')
    ap.add_argument('--min-vcpu', type=int, default=0)
    ap.add_argument('--min-disk', type=float, default=0.0, help='GiB of SSD required')
    ap.add_argument('--family', default=None, help='Basic, CPU-Optimized, General Purpose, Memory-Optimized, Storage-Optimized')
    ap.add_argument('--hours', type=float, default=None, help='hours the droplet will run, for short-lived jobs')
    ap.add_argument('--count', type=int, default=1, help='how many identical droplets')
    ap.add_argument('--backups', choices=sorted(BACKUP), default=None)
    ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--data', default=os.path.join(HERE, 'do-prices.json'))
    a = ap.parse_args()

    d = json.load(open(a.data))
    plans = [p for p in d['plans']
             if (p['memory_gib'] or 0) >= a.min_ram
             and (p['vcpus'] or 0) >= a.min_vcpu
             and (p['disk_gib'] or 0) >= a.min_disk
             and (a.family is None or p['family'].lower() == a.family.lower())]
    if not plans:
        print('No Droplet on the page meets those requirements. Lower a bound or widen --family.')
        return

    out = []
    for p in plans:
        # what a month actually costs at the hourly rate, for a droplet left running
        monthly_by_hour = p['usd_per_hour'] * HOURS_PER_MONTH
        if a.hours:
            base = p['usd_per_hour'] * a.hours
            basis = f'{a.hours:g} h at ${p["usd_per_hour"]:.5f}/h'
        else:
            base = p['usd_per_month']
            basis = f"${p['usd_per_month']:.2f}/mo list"
        extra = BACKUP[a.backups] * base if a.backups else 0.0
        total = (base + extra) * a.count
        out.append({**p, 'basis': basis, 'base_usd': round(base, 2), 'backups_usd': round(extra, 2),
                    'total_usd': round(total, 2), 'monthly_by_hour': round(monthly_by_hour, 2)})
    out.sort(key=lambda x: x['total_usd'])

    if a.json:
        print(json.dumps({'as_of': d['fetched'], 'requirements': {'min_ram': a.min_ram, 'min_vcpu': a.min_vcpu,
                         'min_disk': a.min_disk, 'family': a.family, 'hours': a.hours, 'count': a.count,
                         'backups': a.backups}, 'results': out[:a.top]}, indent=1))
        return

    print(f"Cheapest DigitalOcean Droplet for >= {a.min_ram:g} GiB RAM, >= {a.min_vcpu} vCPU, "
          f">= {a.min_disk:g} GiB disk" + (f" ({a.family})" if a.family else "") +
          f" — prices read {d['fetched']}")
    print(f"{'family':18} {'RAM':>7} {'vCPU':>5} {'disk':>7} {'$/mo':>7} {'$/h':>9} "
          f"{'at 730h':>8} {'total':>8}")
    for x in out[:a.top]:
        print(f"{x['family']:18} {x['memory_gib']:6.3g}G {x['vcpus']:5d} {x['disk_gib']:6.0f}G "
              f"{x['usd_per_month']:7.2f} {x['usd_per_hour']:9.5f} {x['monthly_by_hour']:8.2f} {x['total_usd']:8.2f}")
    best = out[0]
    print()
    print(f"Cheapest: {best['family']} {best['memory_gib']:g} GiB / {best['vcpus']} vCPU / "
          f"{best['disk_gib']:g} GiB SSD — ${best['usd_per_month']:.2f}/mo list, "
          f"${best['usd_per_hour']:.5f}/h.")
    if not a.hours:
        gap = best['monthly_by_hour'] - best['usd_per_month']
        print(f"Left running for a full month (730 h) the hourly rate sums to ${best['monthly_by_hour']:.2f} "
              f"({'+' if gap >= 0 else ''}{gap:.2f} vs the list monthly price). The page's own wording is "
              f"\"monthly caps\", so the monthly figure is a ceiling rather than a sum — check it on your invoice "
              f"before you budget on it.")
    if a.backups:
        print(f"With {a.backups} backups at {BACKUP[a.backups]:.0%} of droplet cost: "
              f"+${best['backups_usd']:.2f}/mo per droplet.")
    if a.count > 1:
        print(f"{a.count} droplets: ${best['total_usd']:.2f} total.")
    print(f"\n{len(plans)} plans met the requirement; the table shows the {min(a.top, len(plans))} cheapest.")
    print('DigitalOcean signup link (affiliate link — a9n9 earns a $25 credit if you sign up and spend $25, '
          'at no extra cost to you): https://m.do.co/c/5050ec0d76ba')


if __name__ == '__main__':
    main()
