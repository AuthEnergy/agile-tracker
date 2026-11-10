#!/usr/bin/env python3
"""Does Elexon's Market Index Price (MID) reproduce Octopus Agile exactly?

Pulls the last N days of Agile prices (region C by default) and Elexon MID, lines them up
by half-hour, fits   agile_inc_vat_p = m * (MID_GBP_per_MWh / 10) + adder * is_peak + c
and reports the error.  Also reports how far ahead MID is published (for the
"tomorrow before 4pm" question).  Standard library only, Python 3.9+.

    python3 check_elexon_price.py            # 10 days, region C
    python3 check_elexon_price.py --days 21 --region A
"""
import argparse, json, os, ssl, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
UTC = timezone.utc
OCTO = "https://api.octopus.energy/v1"
ELEXON = "https://data.elexon.co.uk/bmrs/api/v1"


def _ssl_context():
    """Verified TLS. python.org builds on macOS ship without CA certs, so fall back to
    certifi or the macOS system bundle instead of failing (verification is never disabled)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for p in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/private/etc/ssl/cert.pem"):
            if os.path.exists(p):
                return ssl.create_default_context(cafile=p)
    return ctx


_CTX = _ssl_context()


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "agile-outlook-check/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.load(r)


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- Octopus ----------
def find_agile_product():
    page = f"{OCTO}/products/?brand=OCTOPUS_ENERGY&is_variable=true&page_size=100"
    best = None
    while page:
        d = get(page)
        for p in d["results"]:
            if p.get("display_name") == "Agile Octopus" and not p.get("is_business") and not p.get("is_prepay"):
                if best is None or (p.get("available_to") is None and best.get("available_to") is not None):
                    best = p
        page = d.get("next")
    if not best:
        raise SystemExit("could not find the Agile Octopus product")
    return best["code"]


def agile_rates(product, region, start, end):
    prod = get(f"{OCTO}/products/{product}/")
    code = prod["single_register_electricity_tariffs"]["_" + region]["direct_debit_monthly"]["code"]
    q = urllib.parse.urlencode({"period_from": iso(start), "period_to": iso(end), "page_size": 1500})
    d = get(f"{OCTO}/products/{product}/electricity-tariffs/{code}/standard-unit-rates/?{q}")
    out = {}
    for r in d["results"]:
        t = datetime.fromisoformat(r["valid_from"].replace("Z", "+00:00")).astimezone(UTC)
        out[t] = (r["value_inc_vat"], r["value_exc_vat"])
    return out, code


# ---------- Elexon ----------
def sp_start_utc(settlement_date, sp):
    y, m, d = map(int, settlement_date.split("-"))
    midnight = datetime(y, m, d, tzinfo=UK).astimezone(UTC)
    return midnight + timedelta(minutes=30 * (sp - 1))


def elexon_mid(start, end):
    """Return {slot_start_utc: {provider: (price, volume)}} from the Market Index dataset."""
    out, cur = {}, start
    while cur < end:
        nxt = min(cur + timedelta(days=4), end)
        q = urllib.parse.urlencode({"from": iso(cur), "to": iso(nxt), "format": "json"})
        # settlement-period based endpoint first, generic dataset endpoint as fallback
        rows = None
        for url in (f"{ELEXON}/balancing/pricing/market-index?{q}",
                    f"{ELEXON}/datasets/MID?{urllib.parse.urlencode({'publishDateTimeFrom': iso(cur), 'publishDateTimeTo': iso(nxt), 'format': 'json'})}"):
            try:
                d = get(url)
                rows = d["data"] if isinstance(d, dict) and "data" in d else d
                if rows:
                    break
            except Exception as e:  # noqa
                print(f"  note: {url.split('?')[0]} failed: {e}", file=sys.stderr)
        for r in rows or []:
            t = sp_start_utc(r["settlementDate"], int(r["settlementPeriod"]))
            out.setdefault(t, {})[r.get("dataProvider", "?")] = (float(r["price"]), float(r.get("volume") or 0))
        cur = nxt
    return out


def pick_price(by_provider):
    """Prefer APX with volume, else any provider with volume, else APX/first."""
    withvol = {k: v for k, v in by_provider.items() if v[1] > 0}
    pool = withvol or by_provider
    for k in pool:
        if k.upper().startswith("APX"):
            return pool[k][0]
    return next(iter(pool.values()))[0]


# ---------- fitting ----------
def ols(rows, ys):
    """Least squares for y = X b, X given as list of rows. Tiny Gaussian elimination."""
    n = len(rows[0])
    A = [[sum(r[i] * r[j] for r in rows) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda k: abs(A[k][i]))
        A[i], A[piv], b[i], b[piv] = A[piv], A[i], b[piv], b[i]
        if abs(A[i][i]) < 1e-12:
            return None
        for k in range(i + 1, n):
            f = A[k][i] / A[i][i]
            for j in range(i, n):
                A[k][j] -= f * A[i][j]
            b[k] -= f * b[i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (b[i] - sum(A[i][j] * x[j] for j in range(i + 1, n))) / A[i][i]
    return x


def is_peak(t_utc):
    h = t_utc.astimezone(UK)
    return 1.0 if 16 <= h.hour < 19 else 0.0


def fit(pairs, shift_slots=0, cap=99.0):
    """pairs: sorted list of (t, agile_inc, mid_gbp_mwh) -> fit & errors using mid shifted by shift_slots."""
    byt = {t: (a, m) for t, a, m in pairs}
    X, Y, T = [], [], []
    for t, (a, _) in byt.items():
        m = byt.get(t + timedelta(minutes=30 * shift_slots))
        if not m or a >= cap:       # skip capped slots, they hide the true value
            continue
        X.append([m[1] / 10.0, is_peak(t), 1.0]); Y.append(a); T.append(t)
    if len(X) < 20:
        return None
    b = ols(X, Y)
    if not b:
        return None
    res = [y - sum(bi * xi for bi, xi in zip(b, x)) for x, y in zip(X, Y)]
    mae = sum(abs(r) for r in res) / len(res)
    return {"m": b[0], "adder": b[1], "c": b[2], "mae": mae, "max": max(abs(r) for r in res), "n": len(res)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--region", default="C")
    a = ap.parse_args()

    now = datetime.now(UTC)
    start = (now - timedelta(days=a.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(days=2)

    print(f"Agile region {a.region}, {a.days} days to {now:%Y-%m-%d %H:%MZ}")
    product = find_agile_product()
    rates, code = agile_rates(product, a.region, start, end)
    print(f"  Octopus: {code}, {len(rates)} half-hours, latest {max(rates):%Y-%m-%d %H:%MZ}")

    mid = elexon_mid(start, end)
    if not mid:
        raise SystemExit("Elexon returned no MID rows - endpoint path may have changed; paste the error above.")
    provs = sorted({p for v in mid.values() for p in v})
    print(f"  Elexon MID: {len(mid)} half-hours, providers {provs}, latest {max(mid):%Y-%m-%d %H:%MZ}")

    # how far ahead is MID published?
    tomorrow = (datetime.now(UK) + timedelta(days=1)).date()
    n_tom = sum(1 for t in mid if t.astimezone(UK).date() == tomorrow)
    print(f"  MID rows already published for tomorrow ({tomorrow}): {n_tom}   (Agile rows for tomorrow: "
          f"{sum(1 for t in rates if t.astimezone(UK).date() == tomorrow)})")
    print(f"  Time now (UK): {datetime.now(UK):%H:%M}  <- run this at ~13:30 and ~16:30 to see when tomorrow appears")

    pairs = sorted((t, rates[t][0], pick_price(mid[t])) for t in rates if t in mid and t <= now)
    print(f"  matched half-hours: {len(pairs)}")
    if len(pairs) < 100:
        raise SystemExit("too few matched slots to fit")

    print("\nAlignment test (shift MID by N half-hours; best should be 0):")
    best = None
    for s in range(-3, 4):
        f = fit(pairs, s)
        if f:
            print(f"  shift {s:+d}: MAE {f['mae']:.3f} p   max {f['max']:.2f} p   n={f['n']}")
            if best is None or f["mae"] < best[1]["mae"]:
                best = (s, f)
    s, f = best
    print(f"\nBest fit (shift {s:+d}):  agile_p = {f['m']:.4f} x (MID GBP/MWh /10) + {f['adder']:.3f} x peak + {f['c']:.3f}")
    print(f"  MAE {f['mae']:.3f} p, worst slot {f['max']:.2f} p over {f['n']} uncapped slots")
    verdict = ("EXACT - MID reproduces Agile (error ~ rounding)." if f["mae"] < 0.05 else
               "CLOSE - usable, but expect a small residual; keep residual noise in the forecast." if f["mae"] < 0.5 else
               "POOR - MID is not what Agile is built from; derive wholesale from Agile itself instead.")
    print("Verdict:", verdict)

    print("\nLast 8 matched slots (UK time, Agile p inc VAT, MID GBP/MWh, fitted p):")
    for t, ag, m in pairs[-8:]:
        fitted = f["m"] * (m / 10) + f["adder"] * is_peak(t) + f["c"]
        print(f"  {t.astimezone(UK):%a %d %H:%M}  agile {ag:7.3f}  mid {m:8.2f}  fit {fitted:7.3f}")


if __name__ == "__main__":
    main()
