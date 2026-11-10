#!/usr/bin/env python3
"""Does NESO's free N2EX GB day-ahead price dataset reproduce Octopus Agile?

Same test as check_elexon_price.py, but with the day-ahead auction price from the NESO open
data portal (resource 4f27eea5-7038-4f73-9740-e3e4ad47c26a, "N2EX GB Day-Ahead Price").
The column layout wasn't documented, so this prints the schema and the latest rows first,
then auto-detects the time and price columns.  Needs check_elexon_price.py in the same folder.

    python3 check_dayahead_price.py --days 14 --region C
"""
import argparse, sys, urllib.parse
from datetime import datetime, timedelta, timezone

import check_elexon_price as base  # reuses get(), Octopus helpers, fit()

UTC, UK = base.UTC, base.UK
NESO = "https://api.neso.energy/api/3/action/datastore_search"
RID = "4f27eea5-7038-4f73-9740-e3e4ad47c26a"


def fetch_rows(limit=6000):
    q = urllib.parse.urlencode({"resource_id": RID, "limit": limit, "sort": "_id desc"})
    d = base.get(f"{NESO}?{q}")
    if not d.get("success"):
        raise SystemExit(f"NESO error: {d}")
    return d["result"]["fields"], d["result"]["records"], d["result"].get("total")


def parse_dt(s):
    s = str(s).strip().replace("Z", "+00:00")
    for f in (None, "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.fromisoformat(s.replace(" ", "T")) if f is None else datetime.strptime(s, f)
        except Exception:
            continue
    return None


def detect(fields, rows):
    names = [f["id"] for f in fields if f["id"] != "_id"]
    low = {n: n.lower() for n in names}
    price = next((n for n in names if "price" in low[n] and "volume" not in low[n]), None)
    if not price:  # first numeric-looking column that isn't a period/hour
        for n in names:
            if all(k not in low[n] for k in ("date", "time", "period", "hour")):
                try:
                    float(rows[0][n]); price = n; break
                except Exception:
                    pass
    dtcol = next((n for n in names if any(k in low[n] for k in ("datetime", "timestamp", "delivery_start", "start"))), None)
    datecol = next((n for n in names if "date" in low[n] and n != dtcol), None)
    percol = next((n for n in names if any(k in low[n] for k in ("period", "hour"))), None)
    return price, dtcol, datecol, percol


def to_slots(rows, price, dtcol, datecol, percol):
    """-> {slot_start_utc: GBP/MWh}, plus the median spacing in minutes."""
    pts = []
    for r in rows:
        try:
            p = float(r[price])
        except Exception:
            continue
        t = None
        if dtcol:
            t = parse_dt(r[dtcol])
        elif datecol:
            d = parse_dt(r[datecol])
            if d is not None and percol is not None and ":" in str(r[percol]):
                # "22:00 - 23:00" style delivery period (UK local clock) -> start of the period
                try:
                    hh, mm = str(r[percol]).split("-")[0].strip().split(":")[:2]
                    t = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=UK)
                except Exception:
                    continue
            elif d is not None and percol is not None:
                try:
                    k = int(float(r[percol]))
                except Exception:
                    continue
                span = 60 if "hour" in percol.lower() else 30
                base_t = datetime(d.year, d.month, d.day, tzinfo=UK).astimezone(UTC)
                t = base_t + timedelta(minutes=span * (k - (0 if "hour" in percol.lower() and k == 0 else 1)))
            else:
                t = d
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)          # alignment test below absorbs a 1h BST offset
        pts.append((t.astimezone(UTC), p))
    pts.sort()
    gaps = sorted((b[0] - a[0]).total_seconds() / 60 for a, b in zip(pts, pts[1:]) if b[0] > a[0])
    step = gaps[len(gaps) // 2] if gaps else 60
    out = {}
    for t, p in pts:
        out[t] = p
        if step >= 60:                          # hourly price applies to both half-hours
            out[t + timedelta(minutes=30)] = p
    return out, step


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--region", default="C")
    a = ap.parse_args()

    fields, rows, total = fetch_rows()
    print(f"NESO day-ahead dataset: {total} rows total, fetched {len(rows)} (newest first)")
    print("columns:", [f["id"] + ":" + f["type"] for f in fields])
    for r in rows[:4]:
        print("  ", {k: v for k, v in r.items() if k != "_id"})
    price, dtcol, datecol, percol = detect(fields, rows)
    print(f"detected -> price: {price}, datetime: {dtcol}, date: {datecol}, period/hour: {percol}")
    if not price:
        raise SystemExit("could not detect a price column - paste the output above")
    da, step = to_slots(rows, price, dtcol, datecol, percol)
    if not da:
        raise SystemExit("could not build time slots - paste the output above")
    print(f"day-ahead slots: {len(da)}, source spacing {step:.0f} min, newest {max(da):%Y-%m-%d %H:%MZ}")

    now = datetime.now(UTC)
    tomorrow = (datetime.now(UK) + timedelta(days=1)).date()
    n_tom = sum(1 for t in da if t.astimezone(UK).date() == tomorrow)
    print(f"day-ahead rows for tomorrow ({tomorrow}): {n_tom}   <- run again at ~11:00, 13:30 and 16:30 UK time")

    start = (now - timedelta(days=a.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    product = base.find_agile_product()
    rates, code = base.agile_rates(product, a.region, start, now + timedelta(days=2))
    print(f"Octopus {code}: {len(rates)} half-hours, latest {max(rates):%Y-%m-%d %H:%MZ}")
    n_tom_ag = sum(1 for t in rates if t.astimezone(UK).date() == tomorrow)
    print(f"Agile rows for tomorrow: {n_tom_ag}")

    pairs = sorted((t, rates[t][0], da[t]) for t in rates if t in da and t <= now)
    print(f"matched half-hours: {len(pairs)}")
    if len(pairs) < 100:
        raise SystemExit("too few matched slots - timestamps may be local time; paste the output above")

    print("\nAlignment test (shift day-ahead by N half-hours; best should be 0, or +-2 if the file is in UK local time):")
    best = None
    for s in range(-6, 7):
        f = base.fit(pairs, s)
        if f:
            print(f"  shift {s:+d}: MAE {f['mae']:.3f} p   max {f['max']:.2f} p   n={f['n']}")
            if best is None or f["mae"] < best[1]["mae"]:
                best = (s, f)
    s, f = best
    print(f"\nBest fit (shift {s:+d}):  agile_p = {f['m']:.4f} x (day-ahead GBP/MWh /10) + {f['adder']:.3f} x peak + {f['c']:.3f}")
    print(f"  MAE {f['mae']:.3f} p, worst slot {f['max']:.2f} p over {f['n']} uncapped slots")
    used = [ag for t, ag, m in pairs if ag < 99]
    mean_ag = sum(used) / len(used)
    print(f"  mean Agile price in sample {mean_ag:.2f} p  ->  MAE is {100*f['mae']/mean_ag:.1f}% of the average price, worst slot {f['max']:.1f} p")
    print("Verdict:", "EXACT - day-ahead price reproduces Agile." if f["mae"] < 0.05 else
          "CLOSE - good enough to use, keep a small residual." if f["mae"] < 0.5 else
          "POOR - not the series Agile uses; derive the reference index from Agile itself.")

    # hourly test: average Agile over each clock hour, compare with the hourly auction price
    hours = {}
    for t, ag, m in pairs:
        h = t.replace(minute=0)
        hours.setdefault(h, []).append((ag, m))
    hp = sorted((h, sum(x[0] for x in v) / 2, v[0][1]) for h, v in hours.items() if len(v) == 2)
    hp_pairs = [(h, ag, m) for h, ag, m in hp]
    # fit on hour starts only (is_peak is constant within an hour)
    import check_elexon_price as b2
    X = [[m / 10.0, b2.is_peak(h), 1.0] for h, ag, m in hp_pairs if ag < 99]
    Y = [ag for h, ag, m in hp_pairs if ag < 99]
    if len(X) > 30:
        co = b2.ols(X, Y)
        res = [y - sum(c * x for c, x in zip(co, row)) for row, y in zip(X, Y)]
        # constrained: no offset, adder fixed at 12p
        X2 = [[row[0]] for row in X]
        Y2 = [y - 12.0 * row[1] for row, y in zip(X, Y)]
        m2 = b2.ols(X2, Y2)
        res2 = [y - m2[0] * row[0] for row, y in zip(X2, Y2)]
        print(f"\nHOURLY test (Agile averaged per clock hour, n={len(X)} hours):")
        print(f"  free fit:        agile_p = {co[0]:.4f} x da/10 + {co[1]:.3f} x peak + {co[2]:.3f}   MAE {sum(map(abs, res))/len(res):.3f} p ({100*sum(map(abs, res))/len(res)/(sum(Y)/len(Y)):.1f}%), max {max(map(abs, res)):.2f} p")
        print(f"  constrained fit: agile_p = {m2[0]:.4f} x da/10 + 12.000 x peak + 0.000   MAE {sum(map(abs, res2))/len(res2):.3f} p, max {max(map(abs, res2)):.2f} p")

    print("\nLast 10 matched slots (UK time, Agile p, day-ahead GBP/MWh, fitted p):")
    for t, ag, m in pairs[-10:]:
        print(f"  {t.astimezone(UK):%a %d %H:%M}  agile {ag:7.3f}  da {m:8.2f}  fit {f['m']*(m/10)+f['adder']*base.is_peak(t)+f['c']:7.3f}")


if __name__ == "__main__":
    main()
