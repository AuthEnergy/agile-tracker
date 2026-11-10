#!/usr/bin/env python3
"""Are all Agile regions an exact straight-line function of region C?

Fits, per region R and separately for off-peak and peak (16:00-19:00 UK) half-hours:
    price_R = a * price_C + b
and reports how exact it is.  Slots where either price is at/near the 100p cap are skipped.
Needs check_elexon_price.py in the same folder.

    python3 check_regions.py --days 30
"""
import argparse
from datetime import datetime, timedelta, timezone

import check_elexon_price as base

REGIONS = list("ABCDEFGHJKLMNP")


def line(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-9:
        return None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    res = [y - (a * x + b) for x, y in zip(xs, ys)]
    return a, b, sum(map(abs, res)) / n, max(map(abs, res)), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=a.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    product = base.find_agile_product()

    data = {}
    for r in REGIONS:
        try:
            rates, _ = base.agile_rates(product, r, start, now)
            data[r] = {t: v[0] for t, v in rates.items()}
        except Exception as e:  # noqa
            print(f"  region {r}: failed ({e})")
    ref = data["C"]
    print(f"Reference region C: {len(ref)} half-hours over {a.days} days; product {product}\n")
    print("region  bucket    a(slope)  b(offset p)   MAE p   max p     n")
    for r in REGIONS:
        if r == "C" or r not in data:
            continue
        for name, peak in (("off-peak", 0.0), ("peak", 1.0)):
            ts = [t for t in ref if t in data[r] and base.is_peak(t) == peak and ref[t] < 95 and data[r][t] < 95]
            f = line([ref[t] for t in ts], [data[r][t] for t in ts]) if len(ts) > 20 else None
            if f:
                print(f"  {r}     {name:8s}  {f[0]:8.4f}  {f[1]:10.3f}   {f[2]:6.3f}  {f[3]:6.2f}  {f[4]:5d}")
    print("\nMAE near 0.01-0.05 p means exact (just rounding).  Larger means the straight line is not enough.")
    capped = sum(1 for v in ref.values() if v >= 95)
    print(f"Slots at/near the cap in region C over this period: {capped} of {len(ref)}")


if __name__ == "__main__":
    main()
