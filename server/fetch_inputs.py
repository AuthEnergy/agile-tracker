#!/usr/bin/env python3
"""Download and cache the history the 14-day model trains on.  Stdlib only.

    python3 fetch_inputs.py                # last 12 months of weather, cache in ./cache
    python3 fetch_inputs.py --since 2024-06-01 --refresh

Safe to re-run: existing files are kept unless --refresh.  Ends with a summary of what each file holds.
Needs check_elexon_price.py in the same folder (for the verified TLS context and get()).
"""
import argparse, csv, io, json, os, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

import check_elexon_price as base

CACHE = "cache"
NESO_DUMP = "https://api.neso.energy/datastore/dump/"
RES = {
    "n2ex": "4f27eea5-7038-4f73-9740-e3e4ad47c26a",
    "demand_forecast_archive": "4dd712a2-ee2c-455d-a9c0-9d3564c80fa0",
    "demand_half_hourly_now": "7c0411cd-2714-4bb5-a408-adb065edf34d",
    "wind_14d_now": "93c3048e-1dab-4057-a2a9-417540583929",
    "embedded_now": "db6c038f-98af-4570-ab60-24d71ebd0ae5",
}
OPTIONAL = {  # large archives, only with --with-embedded
    "embedded_2025": "fc13df13-2dad-4a1c-b9e3-4569efba4955",
    "embedded_2026": "31861619-0b86-47ba-bac2-d008a760af54",
}
# Representative UK wind/temperature/solar points (onshore + offshore + population centres)
POINTS = {
    "london": (51.5, -0.1), "manchester": (53.5, -2.2), "edinburgh": (55.95, -3.2),
    "north_sea": (54.0, 1.5), "irish_sea": (53.8, -4.0), "aberdeen_off": (57.2, -1.5),
}


def get_text(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "agile-outlook/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=base._CTX) as r:
        return r.read().decode("utf-8-sig")


def path(name):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, name)


def have(p, refresh):
    return os.path.exists(p) and os.path.getsize(p) > 0 and not refresh


def neso_csv(name, refresh):
    p = path(name + ".csv")
    if have(p, refresh):
        return p
    print(f"  downloading NESO {name} ...", flush=True)
    txt = get_text(NESO_DUMP + (RES.get(name) or OPTIONAL[name]), timeout=90)
    open(p, "w", encoding="utf-8").write(txt)
    time.sleep(0.5)
    return p


def openmeteo_previous_runs(since, refresh):
    """Hourly UK weather: the actual-ish value (day0) and what was forecast 1..7 days earlier."""
    out = []
    end = date.today()
    for name, (lat, lon) in POINTS.items():
        p = path(f"om_prev_{name}.json")
        if have(p, refresh):
            out.append(p)
            continue
        rows, cur = {}, since
        while cur < end:
            nxt = min(cur + timedelta(days=60), end)
            hv = ["wind_speed_100m", "temperature_2m", "shortwave_radiation"]
            vars_ = list(hv)
            for k in range(1, 8):
                vars_ += [f"wind_speed_100m_previous_day{k}"]
            for k in (1, 3, 5, 7):
                vars_ += [f"temperature_2m_previous_day{k}", f"shortwave_radiation_previous_day{k}"]
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": cur.isoformat(),
                                        "end_date": nxt.isoformat(), "hourly": ",".join(vars_),
                                        "wind_speed_unit": "ms", "timezone": "UTC"})
            try:
                d = base.get("https://previous-runs-api.open-meteo.com/v1/forecast?" + q, timeout=120)
            except Exception as e:  # noqa
                print(f"    {name} {cur}..{nxt} failed: {e}")
                cur = nxt
                time.sleep(2)
                continue
            h = d["hourly"]
            for i, t in enumerate(h["time"]):
                rows[t] = {k: h[k][i] for k in h if k != "time"}
            print(f"    {name}: {cur} -> {nxt} ok ({len(rows)} hours so far)", flush=True)
            cur = nxt
            time.sleep(1.0)
        json.dump(rows, open(p, "w"))
        print(f"  Open-Meteo {name}: {len(rows)} hours")
        out.append(p)
    return out


def ttf(refresh):
    """Daily TTF gas price.  Tries several free sources, logs every attempt to cache/gas_diagnostics.txt."""
    p = path("ttf.csv")
    if have(p, refresh):
        return p
    log = []
    now = int(time.time())

    def note(msg):
        log.append(msg)
        print("   gas:", msg, flush=True)

    def save(rows, src):
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["date", "close_eur_mwh"]); w.writerows(rows)
        note(f"USING {src}: {len(rows)} rows {rows[0][0]} -> {rows[-1][0]}")
        open(path("gas_diagnostics.txt"), "w").write("\n".join(log))
        return p

    # 1) Yahoo chart API, several query shapes and hosts
    for host in ("query1", "query2"):
        for sym in ("TTF=F",):
            for qs in (f"range=max&interval=1d", f"period1=1500000000&period2={now}&interval=1d",
                       f"range=5y&interval=1d", f"range=2y&interval=1d&includePrePost=false"):
                url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}?{qs}"
                try:
                    d = base.get(url)
                    r = d["chart"]["result"][0]
                    ts = r.get("timestamp") or []
                    cl = r["indicators"]["quote"][0].get("close") or []
                    rows = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c) for t, c in zip(ts, cl) if c is not None]
                    note(f"yahoo {host} {sym} {qs}: {len(rows)} rows" + (f" {rows[0][0]}->{rows[-1][0]}" if rows else ""))
                    if len(rows) > 200:
                        return save(rows, f"yahoo {host} {qs}")
                except Exception as e:  # noqa
                    note(f"yahoo {host} {sym} {qs}: FAILED {e}")
    # 2) Stooq CSV (a few possible tickers)
    for sym in ("ttf.f", "ttf.c", "tg.f"):
        url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
        try:
            txt = get_text(url, timeout=30)
            lines = [l for l in txt.strip().splitlines() if l and l[0].isdigit()]
            rows = [(l.split(",")[0], float(l.split(",")[4])) for l in lines]
            note(f"stooq {sym}: {len(rows)} rows" + (f" {rows[0][0]}->{rows[-1][0]}" if rows else f" (reply: {txt[:60]!r})"))
            if len(rows) > 200:
                return save(rows, f"stooq {sym}")
        except Exception as e:  # noqa
            note(f"stooq {sym}: FAILED {e}")
    # 3) monthly World Bank EU gas series via Eco3min (too coarse for daily use, but shows the level trend)
    try:
        txt = get_text("https://eco3min.fr/dataset/ttf-gas-price.csv", timeout=30)
        note(f"eco3min monthly TTF: {len(txt.splitlines())-1} lines, head {txt.splitlines()[:2]}")
        open(path("ttf_monthly.csv"), "w").write(txt)
    except Exception as e:  # noqa
        note(f"eco3min monthly: FAILED {e}")
    open(path("gas_diagnostics.txt"), "w").write("\n".join(log))
    print("  TTF daily history unavailable from free sources; see cache/gas_diagnostics.txt")
    return None


def summarise(p):
    if p.endswith(".csv"):
        with open(p, encoding="utf-8") as f:
            rd = csv.reader(f)
            head = next(rd, [])
            n, first, last = 0, None, None
            for row in rd:
                n += 1
                first = first or row
                last = row
        print(f"  {os.path.basename(p)}: {n} rows, columns {head}")
        if n:
            print(f"     first: {first[:6]}\n     last:  {last[:6]}")
    else:
        d = json.load(open(p))
        ks = sorted(d)
        print(f"  {os.path.basename(p)}: {len(ks)} hours {ks[0] if ks else ''} -> {ks[-1] if ks else ''}; "
              f"fields {list(d[ks[0]])[:5] if ks else ''}...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=(date.today() - timedelta(days=365)).isoformat(), help="weather history start (default: 12 months ago)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--with-embedded", action="store_true", help="also download the big embedded wind/solar archives")
    a = ap.parse_args()
    since = date.fromisoformat(a.since)
    got = []
    print("NESO datasets")
    for name in list(RES) + (list(OPTIONAL) if a.with_embedded else []):
        try:
            got.append(neso_csv(name, a.refresh))
        except Exception as e:  # noqa
            print(f"  {name} FAILED: {e}")
    print("Open-Meteo previous-runs (6 UK points)")
    got += openmeteo_previous_runs(since, a.refresh)
    print("Gas")
    t = None if os.path.exists(path("sap.csv")) else ttf(a.refresh)   # gas now comes from fetch_gas.py
    if t:
        got.append(t)
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print("== Summary ==")
        for p in got:
            try:
                summarise(p)
            except Exception as e:  # noqa
                print(f"  {p}: could not summarise ({e})")
    print("\n" + buf.getvalue())
    open(path("summary.txt"), "w").write(buf.getvalue())
    print(f"Done. Summary saved to {path('summary.txt')} - no need to paste anything.")


if __name__ == "__main__":
    main()
