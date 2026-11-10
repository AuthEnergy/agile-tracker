#!/usr/bin/env python3
"""Probe every input the 14-day forecast needs: what exists, its columns, how far ahead it reaches.
Needs check_elexon_price.py in the same folder (for get()).   python3 probe_inputs.py
"""
import json, urllib.parse
import check_elexon_price as base

NESO = "https://api.neso.energy/api/3/action"
DATASETS = [
    "14-days-ahead-wind-forecasts",
    "14-days-ahead-operational-metered-wind-forecasts",
    "2-14-days-ahead-national-demand-forecast",
    "embedded-wind-and-solar-forecasts",
    "day-ahead-power-exchange-prices-nordpool",
]


def show(title):
    print("\n" + "=" * 8, title)


def neso():
    for ds in DATASETS:
        show(f"NESO {ds}")
        try:
            pk = base.get(f"{NESO}/package_show?id={ds}")["result"]
        except Exception as e:
            print("  package_show failed:", e)
            continue
        for r in pk.get("resources", []):
            print(f"  - {r.get('name')!r} id={r['id']} fmt={r.get('format')} datastore={r.get('datastore_active')} modified={r.get('last_modified')}")
        # sample the newest-looking active resources (skip historic archive files)
        for r in pk.get("resources", []):
            nm = (r.get("name") or "").lower()
            if not r.get("datastore_active") or "archive" in nm or "historic" in nm:
                continue
            try:
                q = urllib.parse.urlencode({"resource_id": r["id"], "limit": 3, "sort": "_id desc"})
                d = base.get(f"{NESO}/datastore_search?{q}")["result"]
                print(f"  sample {r.get('name')!r}: total={d.get('total')} fields={[f['id'] for f in d['fields']]}")
                for row in d["records"][:2]:
                    print("     ", {k: v for k, v in row.items() if k != "_id"})
            except Exception as e:
                print(f"  sample {r.get('name')!r} failed:", e)


def weather():
    lat, lon = 54.0, -2.0
    tests = {
        "open-meteo 16-day forecast": ("https://api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon, "hourly": "wind_speed_100m,temperature_2m,shortwave_radiation,cloud_cover", "forecast_days": 16, "timezone": "UTC"}),
        "open-meteo ensemble": ("https://ensemble-api.open-meteo.com/v1/ensemble", {"latitude": lat, "longitude": lon, "hourly": "wind_speed_100m", "models": "ecmwf_ifs025", "forecast_days": 15}),
        "open-meteo archived forecasts": ("https://historical-forecast-api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon, "hourly": "wind_speed_100m", "start_date": "2026-10-20", "end_date": "2026-10-25"}),
        "open-meteo previous runs (day 1-7 ago)": ("https://previous-runs-api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon, "hourly": "wind_speed_100m,wind_speed_100m_previous_day3,wind_speed_100m_previous_day7", "past_days": 5, "forecast_days": 1}),
    }
    for name, (url, params) in tests.items():
        show(name)
        try:
            d = base.get(url + "?" + urllib.parse.urlencode(params))
            h = d.get("hourly", {})
            keys = [k for k in h if k != "time"]
            print("  OK  rows:", len(h.get("time", [])), "first:", h.get("time", [None])[0], "last:", h.get("time", [None])[-1])
            print("  series:", keys[:6], "..." if len(keys) > 6 else "")
        except Exception as e:
            print("  FAILED:", e)


def gas():
    show("TTF gas (Yahoo chart)")
    for sym in ("TTF=F", "NG=F"):
        try:
            d = base.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d")
            r = d["chart"]["result"][0]
            closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
            print(f"  {sym}: OK last close {closes[-1]:.2f} {r['meta'].get('currency')} ({len(closes)} days)")
        except Exception as e:
            print(f"  {sym}: FAILED {e}")


def elexon():
    show("Elexon: nuclear / demand / outturn")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    f = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ")
    t = now.strftime("%Y-%m-%dT%H:%MZ")
    for name, url in {
        "FUELHH outturn": f"https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH?publishDateTimeFrom={f}&publishDateTimeTo={t}&format=json",
        "NUCLEAR availability (REMIT-style)": f"https://data.elexon.co.uk/bmrs/api/v1/datasets/UOU2T14D?publishDateTime={t}&format=json",
        "demand day-ahead forecast": "https://data.elexon.co.uk/bmrs/api/v1/forecast/demand/day-ahead/latest?format=json",
    }.items():
        try:
            d = base.get(url)
            rows = d["data"] if isinstance(d, dict) and "data" in d else d
            print(f"  {name}: OK {len(rows)} rows; sample keys {list(rows[0])[:8] if rows else '-'}")
        except Exception as e:
            print(f"  {name}: FAILED {e}")


if __name__ == "__main__":
    neso(); weather(); gas(); elexon()
    print("\nDone.")
