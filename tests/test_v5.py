"""Assert the v5 paragraph in README.md against DigitalOcean's per-resource rates.

The rates are the vendor's, printed on
https://docs.digitalocean.com/products/droplets/details/pricing/ and read 2026-11-09.
Every figure the README quotes for a v5 shape is recomputed here from those four
numbers, and the bundled price each shape is compared against is read out of
do-prices.json rather than restated. Exit 1 on any disagreement.

    python3 tests/test_v5.py
"""
import json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Vendor rates, $/hour, read 2026-11-09 from DigitalOcean's Droplet pricing docs.
CPU = 0.0158219      # shared vCPU, per vCPU
MEM = 0.0040411      # memory, per GiB
DISK = 0.000137      # boot disk, per GiB
HOURS = 730

plans = json.load(open(os.path.join(HERE, "do-prices.json")))["plans"]


def bundled(memory_gib):
    """The bundled plan's monthly price for a given memory size (Basic family first)."""
    for p in plans:
        if p["memory_gib"] == memory_gib and p["family"] == "Basic":
            return p["usd_per_month"]
    for p in plans:
        if p["memory_gib"] == memory_gib:
            return p["usd_per_month"]
    raise KeyError(memory_gib)


def v5(vcpu, gib, disk_gib):
    return vcpu * CPU + gib * MEM + disk_gib * DISK


# (vCPUs, GiB RAM, GiB boot disk, bundled plan GiB to compare against)
SHAPES = [(1, 1, 25, 1), (2, 4, 80, 4), (4, 8, 160, 8)]


def expectations():
    out = []
    for vcpu, gib, disk, cmp_mem in SHAPES:
        hourly = v5(vcpu, gib, disk)
        monthly = round(hourly * HOURS, 2)
        price = bundled(cmp_mem)
        ratio = round(monthly / price, 2)
        out.append((hourly, monthly, price, ratio, (vcpu, gib, disk)))
    return out


def check(text):
    """Return a list of failures for this README text."""
    fails = []
    for hourly, monthly, price, ratio, shape in expectations():
        vcpu, gib, disk = shape
        tag = f"{vcpu} vCPU / {gib} GiB / {disk} GiB"
        # the hourly rate is quoted to six decimals, the month to the cent
        want_hourly = f"${hourly:.6f}/h"
        if want_hourly not in text:
            fails.append(f"{tag}: README does not print hourly {want_hourly}")
        if f"${monthly:.2f}" not in text:
            fails.append(f"{tag}: README does not print {monthly:.2f} a month")
        if f"${price:.2f}" not in text:
            fails.append(f"{tag}: README does not print the bundled ${price:.2f}")
        if f"{ratio:.2f}×" not in text:
            fails.append(f"{tag}: README does not print the {ratio:.2f}x ratio")
    if "not capped at 672 hours" not in text:
        fails.append("README must quote the vendor's own words that v5 is not capped at 672 hours")
    return fails


readme = " ".join(open(os.path.join(HERE, "README.md")).read().split())
fails = check(readme)

# Selftest: the checker must fail on a mutated figure, or it is a mirror, not a check.
mutated = readme.replace("2.83×", "2.38×", 1)
if mutated == readme:
    fails.append("selftest could not mutate the README (2.83x not found)")
elif not check(mutated):
    fails.append("selftest: checker accepted a wrong ratio - it is not checking the figure")

print("v5 shapes checked:", len(expectations()))
for hourly, monthly, price, ratio, shape in expectations():
    print(f"  {shape[0]} vCPU / {shape[1]} GiB / {shape[2]} GiB: ${hourly:.6f}/h = ${monthly:.2f}/mo"
          f" vs bundled ${price:.2f} = {ratio:.2f}x")
if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("ALL V5 CHECKS PASS")
