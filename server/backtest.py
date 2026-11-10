#!/usr/bin/env python3
"""Walk-forward backtest of the 1-14 day wholesale price forecast.

Target: N2EX GB day-ahead hourly price (GBP/MWh).  Everything the model sees for a forecast issued on
day D about day D+h is information that existed on day D:
  * demand forecast made h days ahead (NESO archive, cardinal points)
  * UK weather forecast made h days ahead (Open-Meteo previous-runs, 1..7 days; beyond 7 see --scenario)
  * calendar, and the recent price level (all prices up to and including D)
Scenarios for horizons 8-14 (no archived weather forecast that far ahead exists):
  A  weather skill assumed equal to the 7-day-ahead forecast  (optimistic)
  B  no weather skill at all, climatology only                (pessimistic)
The truth for days 8-14 lies between A and B.

    python3 backtest.py --cache cache
Needs pandas, numpy, scikit-learn.
"""
import argparse, json, os, sys, time
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

POINTS = ["london", "manchester", "edinburgh", "north_sea", "irish_sea", "aberdeen_off"]
HOLIDAYS = pd.to_datetime(["2024-12-25", "2024-12-26", "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05",
                           "2025-05-26", "2025-08-25", "2025-12-25", "2025-12-26", "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04",
                           "2026-05-25", "2026-08-31", "2026-12-25", "2026-12-28"]).date
TZ = "Europe/London"
MAXH = 14


# ------------------------------------------------------------------ loading
def load_prices(cache):
    n = pd.read_csv(os.path.join(cache, "n2ex.csv"))
    start = n["Delivery Period"].str.slice(0, 5)
    local = pd.to_datetime(n["Date"].astype(str) + " " + start)
    ts = local.dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward")
    s = pd.Series(n["Price"].values, index=ts).dropna()
    s = s[~s.index.isna()]
    s.index = s.index.tz_convert("UTC")
    s = s[~s.index.duplicated()].sort_index()
    return s  # hourly, UTC, GBP/MWh


def load_weather(cache):
    frames = []
    for p in POINTS:
        d = json.load(open(os.path.join(cache, f"om_prev_{p}.json")))
        df = pd.DataFrame.from_dict(d, orient="index")
        df.index = pd.to_datetime(df.index, utc=True)
        frames.append(df.sort_index().astype(float))
    cols = frames[0].columns
    out = {}
    for c in cols:
        stack = np.vstack([f[c].values for f in frames])
        if c.startswith("wind_speed"):
            # mean wind speed, plus a "wind power index" that saturates like a turbine power curve
            out[c] = np.nanmean(stack, axis=0)
            out[c.replace("wind_speed_100m", "wpi")] = np.nanmean(np.minimum(stack, 13.0) ** 3 / 13.0 ** 3, axis=0)
        else:
            out[c] = np.nanmean(stack, axis=0)
    return pd.DataFrame(out, index=frames[0].index)


def load_demand(cache):
    d = pd.read_csv(os.path.join(cache, "demand_forecast_archive.csv"))
    d["TARGETDATE"] = pd.to_datetime(d["TARGETDATE"]).dt.date
    g = d.groupby(["TARGETDATE", "DAYSAHEAD"])
    agg = pd.DataFrame({
        "dem_max": g["FORECASTDEMAND"].max(), "dem_min": g["FORECASTDEMAND"].min(),
        "dem_mean": g["FORECASTDEMAND"].mean(),
    })
    ep = d[d["CARDINALPOINT"] == "DP"].groupby(["TARGETDATE", "DAYSAHEAD"])["FORECASTDEMAND"].mean().rename("dem_ep")
    return agg.join(ep)


# ------------------------------------------------------------------ feature table
def build(cache, scenario):
    px = load_prices(cache)
    wx = load_weather(cache)
    dem = load_demand(cache)

    idx = px.index
    local = idx.tz_convert(TZ)
    P = pd.DataFrame({"price": px.values, "ldate": local.date, "lhour": local.hour}, index=idx)
    P["ldate"] = pd.to_datetime(P["ldate"])
    daily = P.groupby("ldate")["price"].mean()
    piv = P.pivot_table(index="ldate", columns="lhour", values="price").reindex(columns=range(24))
    lvl7 = daily.rolling(7, min_periods=5).mean()
    lvl30 = daily.rolling(30, min_periods=20).mean()
    sh7 = piv.rolling(7, min_periods=5).mean()
    wstart = pd.Timestamp(wx.index.min().date())
    ctx = {}
    for D in daily.index:
        if D < wstart or np.isnan(lvl7.loc[D]):
            continue
        ctx[D] = (lvl7.loc[D], lvl30.loc[D], sh7.loc[D].values, daily.loc[D])
    # --- weather lead mapping
    wxl = {}
    for h in range(1, MAXH + 1):
        k = min(h, 7)
        tk = {1: 1, 2: 3, 3: 3, 4: 5, 5: 5, 6: 7, 7: 7}[k]
        wxl[h] = {
            "wind": wx[f"wind_speed_100m_previous_day{k}"], "wpi": wx[f"wpi_previous_day{k}"],
            "temp": wx[f"temperature_2m_previous_day{tk}"], "rad": wx[f"shortwave_radiation_previous_day{tk}"],
        }
    wact = {"wind": wx["wind_speed_100m"], "wpi": wx["wpi"], "temp": wx["temperature_2m"], "rad": wx["shortwave_radiation"]}
    wdaily_act = {k: v.groupby(pd.to_datetime(v.index.tz_convert(TZ).date)).mean() for k, v in wact.items()}

    gas = {}
    gp = os.path.join(cache, "sap.csv")
    if os.path.exists(gp):
        g_ = pd.read_csv(gp, parse_dates=["date"]).set_index("date")["sap_p_per_kwh"]
        g_ = g_.reindex(pd.date_range(g_.index.min(), g_.index.max() + pd.Timedelta(days=60))).ffill(limit=14)
        gl = g_.shift(1)                      # SAP for D-1 is what is known on day D
        gas = {"gas_l": gl, "gas_chg7": gl - gl.shift(7), "gas_dev30": gl - gl.rolling(30, min_periods=20).mean()}
    rows = []
    bydate = {d: g for d, g in P.groupby("ldate")}  # keys are Timestamps
    clim = {}  # trailing 30-day actual weather means as of D (scenario B)
    wdd = {k: v for k, v in wdaily_act.items()}
    roll = {k: v.rolling(30, min_periods=15).mean() for k, v in wdd.items()}
    for D in ctx:
        for k in wdd:
            clim[(D, k)] = roll[k].get(D, np.nan)
    for D in ctx:
        level7, level30, sh24, last_day = ctx[D]
        for h in range(1, MAXH + 1):
            T = D + pd.Timedelta(days=h)
            if T not in bydate:
                continue
            g = bydate[T]
            if len(g) < 20:
                continue
            idxh = g.index
            use_clim = scenario == "B" and h > 7
            feat = {}
            for k in ("wind", "wpi", "temp", "rad"):
                if use_clim:
                    hv = np.full(len(idxh), clim[(D, k)])
                    dm = clim[(D, k)]
                else:
                    ser = wxl[h][k]
                    hv = ser.reindex(idxh).values
                    dm = np.nanmean(hv) if len(hv) else np.nan
                feat[k] = hv
                feat[k + "_day"] = np.full(len(idxh), dm)
            dl = max(h, 2)
            try:
                dr = dem.loc[(T.date(), dl)]
                dvals = (dr["dem_max"], dr["dem_min"], dr["dem_mean"], dr.get("dem_ep", np.nan))
            except KeyError:
                dvals = (np.nan,) * 4
            lh = g["lhour"].values
            n = len(g)
            ang = 2 * np.pi * T.dayofyear / 365.25
            rows.append(pd.DataFrame({
                "issue": D, "target": T, "h": h, "hour": lh, "dow": T.dayofweek,
                "hol": float(T.date() in HOLIDAYS), "doy_s": np.sin(ang), "doy_c": np.cos(ang),
                **{k: v for k, v in feat.items()},
                "dem_max": dvals[0], "dem_min": dvals[1], "dem_mean": dvals[2], "dem_ep": dvals[3],
                **{k: (v.get(D, np.nan) if len(v) else np.nan) for k, v in gas.items()},
                "level7": level7, "level30": level30, "lastday": last_day, "sh7": sh24[lh], "sh_dev": sh24[lh] - level7,
                "y": g["price"].values,
            }, index=idxh))
    return pd.concat(rows)


# Deliberately small: day-of-year, wide trees and many features all overfit one year of data (see notes in README).
FEATS = ["h", "hour", "dow", "hol", "wpi", "wpi_day", "temp_day", "dem_mean", "dem_min", "sh_dev", "level7", "lastday"]


GASF = ["gas_l", "gas_chg7", "gas_dev30"]
USE_GAS = True


def feats():
    return FEATS + (GASF if USE_GAS else [])


def fit(df):
    m = HistGradientBoostingRegressor(loss="absolute_error", max_iter=120, learning_rate=0.06, max_leaf_nodes=6,
                                      min_samples_leaf=400, l2_regularization=5.0, random_state=0)
    m.fit(df[feats()], np.clip(df["y"] - df["sh7"], -150, 450))
    return m


def walk_forward(df, start_eval, step_days=14):
    df = df.sort_values(["issue", "h"])
    issues = sorted(df["issue"].unique())
    preds = []
    cur = pd.Timestamp(start_eval)
    last_issue = issues[-1]
    while cur <= last_issue:
        nxt = cur + pd.Timedelta(days=step_days)
        # train on rows whose *target* is already known at 'cur'
        tr = df[(df["target"] < cur)]
        te = df[(df["issue"] >= cur) & (df["issue"] < nxt)]
        if len(tr) > 5000 and len(te):
            m = fit(tr)
            te = te.copy()
            te["pred"] = m.predict(te[feats()]) + te["sh7"]
            preds.append(te)
        cur = nxt
    return pd.concat(preds)


def report(pr, label):
    pr = pr.dropna(subset=["y", "pred"])
    mp = pr["y"].mean()
    print(f"\n=== {label}")
    print(f"    {len(pr):,} hourly forecasts, issue dates {pr['issue'].min():%Y-%m-%d} to {pr['issue'].max():%Y-%m-%d}; mean price {mp:.0f} GBP/MWh (~{mp/5:.0f}p Agile)")
    print("    1 GBP/MWh ~ 0.2p/kWh on an Agile price.  'naive' = same hour average of the last 7 days.")
    print("days | hourly MAE: model  naive   | daily-avg MAE: model (%)  naive | direction vs last-7d avg: all  big moves(>20)")
    out = []
    for h in range(1, MAXH + 1):
        s_ = pr[pr["h"] == h]
        if s_.empty:
            continue
        mae = (s_["pred"] - s_["y"]).abs().mean()
        nmae = (s_["sh7"] - s_["y"]).abs().mean()
        dm = s_.groupby("target").agg(p=("pred", "mean"), y=("y", "mean"), l7=("level7", "first"))
        dmae = (dm["p"] - dm["y"]).abs().mean()
        nd = (dm["l7"] - dm["y"]).abs().mean()
        hit = (np.sign(dm["p"] - dm["l7"]) == np.sign(dm["y"] - dm["l7"])).mean()
        big = dm[(dm["y"] - dm["l7"]).abs() > 20]
        bh = (np.sign(big["p"] - big["l7"]) == np.sign(big["y"] - big["l7"])).mean() if len(big) else float("nan")
        print(f"{h:>4} |        {mae:5.1f}  {nmae:5.1f}   |          {dmae:5.1f} ({100*dmae/mp:4.1f}%)  {nd:5.1f} |              {100*hit:4.0f}%        {100*bh:4.0f}% (n={len(big)})")
        out.append({"h": h, "mae": mae, "naive_mae": nmae, "daily_mae": dmae, "daily_mae_pct": 100 * dmae / mp,
                    "daily_naive_mae": nd, "trend_hit": 100 * hit, "big_move_hit": 100 * bh, "days": len(dm)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--eval-from", default="2025-11-01")
    ap.add_argument("--scenarios", default="A,B")
    ap.add_argument("--no-gas", action="store_true")
    a = ap.parse_args()
    global USE_GAS
    USE_GAS = not a.no_gas
    results = {}
    for sc in a.scenarios.split(","):
        t0 = time.time()
        df = build(a.cache, sc)
        print(f"[{sc}] feature table {len(df):,} rows built in {time.time()-t0:.0f}s", flush=True)
        pr = walk_forward(df, a.eval_from)
        results[sc] = report(pr, f"scenario {sc}: " + ("days 8-14 use the 7-day-ahead weather forecast" if sc == "A" else "days 8-14 have no weather skill (climatology)"))
        pr.to_csv(os.path.join(a.cache, f"backtest_pred_{sc}{'_nogas' if a.no_gas else ''}.csv.gz"))
    json.dump(results, open(os.path.join(a.cache, "backtest_results.json"), "w"), indent=1)
    print("\nSaved cache/backtest_results.json")


if __name__ == "__main__":
    main()
