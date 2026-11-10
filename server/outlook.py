#!/usr/bin/env python3
"""Daily 14-day GB wholesale price outlook -> one public national JSON (+ per-region Agile formulas).

    python3 outlook.py --cache cache --out /var/www/far.cx/uk-forecast.json --logs logs
    python3 outlook.py --cache cache --asof 2026-09-15 --out /tmp/test.json     # offline replay of a past day

What a run does
  1. refresh inputs (NESO N2EX / demand, SAP gas via National Gas portal, Open-Meteo history, live weather)
  2. train the small model (backtest.py recipe) on everything known up to today
  3. health gate: a model trained 6 weeks ago must stay within 15% of the naive forecast on the last 6 weeks, else the
     published file falls back to the naive forecast and says so
  4. forecast hourly wholesale GBP/MWh for the next 14 UK days (published day-ahead prices replace the forecast)
  5. refit the per-region Agile formula (weekly), score earlier logged forecasts, write accuracy + uncertainty
  6. log today's forecast to logs/ so real days 8-14 accuracy is measured from now on
Needs pandas, numpy, scikit-learn; check_elexon_price.py, fetch_inputs.py and backtest.py in the same folder.
"""
import argparse, csv, fcntl, io, json, os, sys, time, urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

import backtest as bt
import check_elexon_price as base
import fetch_inputs as fi

TZ = bt.TZ
HORIZON = 14
VERSION = 1
PORTAL = "https://data.nationalgas.com/api/find-gas-data-download"
REGIONS = list("ABCDEFGHJKLMNP")
NOTES = []


def note(m):
    NOTES.append(m)
    print("  note:", m, flush=True)


# ---------------------------------------------------------------- refresh
def atomic_write(path, text):
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8").write(text)
    os.replace(tmp, path)


def refresh(cache):
    fi.CACHE = cache
    os.makedirs(cache, exist_ok=True)
    ok = {}
    for name in ("n2ex", "demand_forecast_archive", "wind_14d_now"):
        try:
            txt = fi.get_text(fi.NESO_DUMP + fi.RES[name], timeout=120)
            if len(txt) > 1000:
                atomic_write(os.path.join(cache, name + ".csv"), txt)
                ok[name] = True
        except Exception as e:  # noqa
            note(f"{name} refresh failed ({e}); using cached copy")
    # SAP gas: last 60 days merged into sap.csv
    try:
        end = date.today()
        q = (f"?ids=PUBOB603&applicableFor=Y&dateType=GASDAY&latestFlag=Y&dateFrom={end - timedelta(days=60)}"
             f"&dateTo={end}&type=CSV")
        req = urllib_request(PORTAL + q)
        new = {}
        for r in csv.DictReader(io.StringIO(req)):
            try:
                new[datetime.strptime(r["Applicable For"][:10], "%d/%m/%Y").date().isoformat()] = float(r["Value"])
            except Exception:
                pass
        gp = os.path.join(cache, "sap.csv")
        old = {}
        if os.path.exists(gp):
            old = {r["date"]: float(r["sap_p_per_kwh"]) for r in csv.DictReader(open(gp))}
        old.update(new)
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(["date", "sap_p_per_kwh"]); w.writerows(sorted(old.items()))
        atomic_write(gp, buf.getvalue())
        ok["sap"] = bool(new)
    except Exception as e:  # noqa
        note(f"SAP gas refresh failed ({e}); gas level carried forward")
    # Open-Meteo history extension (so the newest days become training rows)
    for name, (lat, lon) in fi.POINTS.items():
        p = os.path.join(cache, f"om_prev_{name}.json")
        try:
            rows = json.load(open(p)) if os.path.exists(p) else {}
            last = max(rows)[:10] if rows else (date.today() - timedelta(days=365)).isoformat()
            start = date.fromisoformat(last) - timedelta(days=2)
            end = date.today()
            vars_ = ["wind_speed_100m", "temperature_2m", "shortwave_radiation"]
            vars_ += [f"wind_speed_100m_previous_day{k}" for k in range(1, 8)]
            for k in (1, 3, 5, 7):
                vars_ += [f"temperature_2m_previous_day{k}", f"shortwave_radiation_previous_day{k}"]
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": start.isoformat(),
                                        "end_date": end.isoformat(), "hourly": ",".join(vars_),
                                        "wind_speed_unit": "ms", "timezone": "UTC"})
            d = base.get("https://previous-runs-api.open-meteo.com/v1/forecast?" + q, timeout=120)
            h = d["hourly"]
            for i, t in enumerate(h["time"]):
                row = {k: h[k][i] for k in h if k != "time"}
                if any(v is not None for v in row.values()):
                    rows[t] = row
            atomic_write(p, json.dumps(rows))
        except Exception as e:  # noqa
            note(f"weather history extension failed for {name} ({e})")
    return ok


def urllib_request(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "agile-outlook/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=base._CTX) as r:
        return r.read().decode("utf-8-sig")


def live_weather():
    """Current Open-Meteo forecast (16 days, hourly, UTC) averaged over the 6 UK points."""
    fr = []
    for name, (lat, lon) in fi.POINTS.items():
        q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "forecast_days": 16, "timezone": "UTC",
                                    "hourly": "wind_speed_100m,temperature_2m,shortwave_radiation", "wind_speed_unit": "ms"})
        d = base.get("https://api.open-meteo.com/v1/forecast?" + q, timeout=60)
        h = d["hourly"]
        df = pd.DataFrame({k: h[k] for k in h if k != "time"}, index=pd.to_datetime(h["time"], utc=True)).astype(float)
        fr.append(df)
    ws = np.vstack([f["wind_speed_100m"].values for f in fr])
    return pd.DataFrame({
        "wpi": np.nanmean(np.minimum(ws, 13.0) ** 3 / 13.0 ** 3, axis=0),
        "temp": np.nanmean(np.vstack([f["temperature_2m"].values for f in fr]), axis=0),
        "wind": np.nanmean(ws, axis=0),
    }, index=fr[0].index)


# ---------------------------------------------------------------- inference features
class Ctx:
    """Everything needed to build the forecast rows for issue date D (a naive local date)."""

    def __init__(self, cache, D, asof):
        self.cache, self.D, self.asof = cache, pd.Timestamp(D), asof
        px = bt.load_prices(cache)
        self.px_all = px
        local = px.index.tz_convert(TZ)
        P = pd.DataFrame({"price": px.values, "ldate": pd.to_datetime(local.date), "lhour": local.hour}, index=px.index)
        P = P[P["ldate"] <= self.D]                       # only what is known by the end of D
        self.P = P
        daily = P.groupby("ldate")["price"].mean()
        piv = P.pivot_table(index="ldate", columns="lhour", values="price").reindex(columns=range(24))
        self.level7 = daily.rolling(7, min_periods=5).mean().loc[self.D]
        self.level30 = daily.rolling(30, min_periods=20).mean().loc[self.D]
        self.sh7 = piv.rolling(7, min_periods=5).mean().loc[self.D].values
        self.lastday = daily.loc[self.D]
        self.gas = {}
        gp = os.path.join(cache, "sap.csv")
        if os.path.exists(gp):
            g_ = pd.read_csv(gp, parse_dates=["date"]).set_index("date")["sap_p_per_kwh"]
            if asof:
                g_ = g_[g_.index <= self.D]
            g_ = g_.reindex(pd.date_range(g_.index.min(), self.D + pd.Timedelta(days=60))).ffill(limit=14)
            gl = g_.shift(1)
            self.gas = {"gas_l": gl.get(self.D, np.nan),
                        "gas_chg7": (gl - gl.shift(7)).get(self.D, np.nan),
                        "gas_dev30": (gl - gl.rolling(30, min_periods=20).mean()).get(self.D, np.nan)}
            self.sap_latest = g_.dropna().iloc[-1] if g_.notna().any() else None
        self.dem = pd.read_csv(os.path.join(cache, "demand_forecast_archive.csv"))
        self.dem["TARGETDATE"] = pd.to_datetime(self.dem["TARGETDATE"])
        self.wx = bt.load_weather(cache)
        self.live = None

    def weather(self, h, idx):
        """hourly wpi values at UTC instants idx + the day-mean temperature."""
        if self.live is not None:
            wpi = self.live["wpi"].reindex(idx).values
            tmp = self.live["temp"].reindex(idx).values
        else:                                              # replay: the same lead-time mapping the model was trained on
            k = min(h, 7)
            tk = {1: 1, 2: 3, 3: 3, 4: 5, 5: 5, 6: 7, 7: 7}[k]
            wpi = self.wx[f"wpi_previous_day{k}"].reindex(idx).values
            tmp = self.wx[f"temperature_2m_previous_day{tk}"].reindex(idx).values
        return wpi, (np.nanmean(tmp) if np.isfinite(tmp).any() else np.nan)

    def demand(self, T, h):
        d = self.dem[self.dem["TARGETDATE"] == T]
        if self.asof:
            d = d[d["DAYSAHEAD"] == max(h, 2)]
        elif "FORECAST_TIMESTAMP" in d and d["FORECAST_TIMESTAMP"].notna().any():
            d = d[d["FORECAST_TIMESTAMP"] == d["FORECAST_TIMESTAMP"].max()]
        if d.empty:
            return np.nan, np.nan
        return d["FORECASTDEMAND"].mean(), d["FORECASTDEMAND"].min()

    def rows(self):
        out = []
        for h in range(1, HORIZON + 1):
            T = self.D + pd.Timedelta(days=h)
            idx = pd.date_range(T.tz_localize(TZ), (T + pd.Timedelta(days=1)).tz_localize(TZ), freq="h", inclusive="left").tz_convert("UTC")
            lh = idx.tz_convert(TZ).hour.values
            wpi, tday = self.weather(h, idx)
            dmean, dmin = self.demand(T, h)
            wpi_day = np.nanmean(wpi) if np.isfinite(wpi).any() else np.nan
            df = pd.DataFrame({"issue": self.D, "target": T, "h": h, "hour": lh, "dow": T.dayofweek,
                               "hol": float(T.date() in bt.HOLIDAYS), "wpi": wpi, "wpi_day": wpi_day, "temp_day": tday,
                               "dem_mean": dmean, "dem_min": dmin, "level7": self.level7, "lastday": self.lastday,
                               "sh7": self.sh7[lh], "sh_dev": self.sh7[lh] - self.level7, **self.gas}, index=idx)
            out.append(df)
        return pd.concat(out)


# ---------------------------------------------------------------- model + gate
def train(df, upto_target):
    tr = df[(df["target"] <= upto_target) & df["y"].notna()]
    return bt.fit(tr), len(tr)


def health_gate(df, D):
    """Model trained before D-42d, tested on issues in the last 42 days (h<=7, daily-average MAE) vs naive."""
    cut = D - pd.Timedelta(days=42)
    tr = df[df["target"] < cut]
    te = df[(df["issue"] >= cut) & (df["target"] <= D) & (df["h"] <= 7)].dropna(subset=["y"])
    if len(tr) < 5000 or len(te) < 500:
        return True, {"note": "not enough data for a gate"}
    m = bt.fit(tr)
    te = te.copy()
    te["pred"] = m.predict(te[bt.feats()]) + te["sh7"]
    g = te.groupby(["issue", "target"]).agg(p=("pred", "mean"), y=("y", "mean"), n=("sh7", "mean"))
    mae, nmae = (g["p"] - g["y"]).abs().mean(), (g["n"] - g["y"]).abs().mean()
    return bool(mae < 1.15 * nmae), {"ratio": round(float(mae / nmae), 3), "model_mae": round(float(mae), 2), "naive_mae": round(float(nmae), 2), "days": int(len(g))}


# ---------------------------------------------------------------- uncertainty, scoring, regions
def band_tables(cache, logs_scores):
    """Residual quantiles per horizon (hourly + daily average), measured from logs once there are enough, else backtest."""
    res = {}
    src = {}
    pa = os.path.join(cache, "backtest_pred_A.csv.gz")
    pb = os.path.join(cache, "backtest_pred_B.csv.gz")
    frames = {}
    for k, p in (("A", pa), ("B", pb)):
        if os.path.exists(p):
            frames[k] = pd.read_csv(p, index_col=0, parse_dates=["issue", "target"]).dropna(subset=["y", "pred"])
    for h in range(1, HORIZON + 1):
        ent = None
        if h in logs_scores and logs_scores[h]["n_days"] >= 30:
            s = logs_scores[h]
            ent = dict(hourly=s["hourly_q"], daily=s["daily_q"], bias=s["bias"], source="logged", n=s["n_days"])
        elif frames:
            hq, dq = [], []
            for k, f in frames.items():
                if h > 7 or k == "A":
                    x = f[f["h"] == h]
                    if x.empty:
                        continue
                    hq.append(np.quantile(x["pred"] - x["y"], [0.1, 0.5, 0.9]))
                    d = x.groupby("target").agg(p=("pred", "mean"), y=("y", "mean"))
                    dq.append(np.quantile(d["p"] - d["y"], [0.1, 0.5, 0.9]))
            if hq:
                ent = dict(hourly=np.mean(hq, axis=0).tolist(), daily=np.mean(dq, axis=0).tolist(), bias=0.0,
                           source="backtest" if h <= 7 else "backtest A/B average", n=int(len(frames["A"][frames["A"]["h"] == h]) / 24) if "A" in frames else 0)
        if ent:
            res[h] = ent
    return res


def score_logs(logs_dir, px):
    """Compare logged forecasts with N2EX actuals.  -> {h: stats}"""
    per = {}
    if not os.path.isdir(logs_dir):
        return {}
    for fn in sorted(os.listdir(logs_dir)):
        if not fn.startswith("forecast_") or not fn.endswith(".json"):
            continue
        try:
            L = json.load(open(os.path.join(logs_dir, fn)))
        except Exception:
            continue
        issue = pd.Timestamp(L["issue"])
        for h_s, ent in L["days"].items():
            h = int(h_s)
            if ent.get("published"):
                continue                                    # not a forecast (price was already known)
            t = pd.to_datetime(ent["hours_utc"], utc=True)
            act = px.reindex(t)
            p = np.array(ent["p50"], float)
            ok = act.notna().values
            if ok.sum() < 20:
                continue
            e = p[ok] - act.values[ok]
            d = per.setdefault(h, {"h_err": [], "d_err": [], "d_y": [], "hit": []})
            d["h_err"] += e.tolist()
            d["d_err"].append(float(np.mean(p[ok]) - np.mean(act.values[ok])))
            d["d_y"].append(float(np.mean(act.values[ok])))
            if L.get("level7") is not None:
                d["hit"].append(float(np.sign(np.mean(p[ok]) - L["level7"]) == np.sign(np.mean(act.values[ok]) - L["level7"])))
    out = {}
    for h, d in per.items():
        n = len(d["d_err"])
        if n < 5:
            continue
        he, de = np.array(d["h_err"]), np.array(d["d_err"])
        out[h] = {"n_days": n, "hourly_q": np.quantile(he, [0.1, 0.5, 0.9]).tolist(), "daily_q": np.quantile(de, [0.1, 0.5, 0.9]).tolist(),
                  "bias": float(np.median(he)), "hourly_mae": float(np.mean(np.abs(he))),
                  "daily_mae": float(np.mean(np.abs(de))), "daily_mae_pct": float(100 * np.mean(np.abs(de)) / max(np.mean(d["d_y"]), 1)),
                  "trend_hit": float(100 * np.mean(d["hit"])) if d["hit"] else None}
    return out


def fit_regions(cache, px_all):
    """Per-region Agile = m*(wholesale/10) + adder*peak + c   (p/kWh inc VAT, cap 100); cached for 7 days."""
    cp = os.path.join(cache, "regions.json")
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < 7 * 86400:
        return json.load(open(cp))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
    product = base.find_agile_product()
    da = {}
    for t, p in px_all.items():
        t = t.to_pydatetime()
        da[t] = p
        da[t + timedelta(minutes=30)] = p
    out = {}
    for r in REGIONS:
        rates = {}
        cur = start
        while cur < now:                                     # Octopus pages are capped, fetch in 20-day windows
            nxt = min(cur + timedelta(days=20), now)
            got, _ = base.agile_rates(product, r, cur, nxt)
            rates.update(got)
            cur = nxt
        pairs = sorted((t, v[0], da[t]) for t, v in rates.items() if t in da)
        f = base.fit(pairs, 0)
        if f:
            out[r] = {"m": round(f["m"], 5), "adder": round(f["adder"], 4), "c": round(f["c"], 4), "cap": 100.0,
                      "mae_p": round(f["mae"], 3), "max_p": round(f["max"], 2), "n": f["n"]}
    out["_fitted"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["_product"] = product
    atomic_write(cp, json.dumps(out))
    return out


# ---------------------------------------------------------------- main
def build_summary(days, baseline):
    means = [d["mean"] for d in days]
    wk1, wk2 = float(np.mean(means[:7])), float(np.mean(means[7:14]))
    pct1, pct2 = 100 * (wk1 / baseline - 1), 100 * (wk2 / baseline - 1)
    turn = None
    for i in range(1, len(means) - 1):
        dev = [100 * (m / baseline - 1) for m in means[i:i + 3]]
        if all(x > 10 for x in dev) or all(x < -10 for x in dev):
            turn = {"date": days[i]["date"], "direction": "up" if dev[0] > 0 else "down"}
            break
    cheap = min(range(len(means)), key=lambda i: means[i]); dear = max(range(len(means)), key=lambda i: means[i])
    if pct2 > 7:
        head = f"Prices look set to rise: about {pct2:.0f}% above the last 7 days by days 8-14"
    elif pct2 < -7:
        head = f"Prices look set to fall: about {abs(pct2):.0f}% below the last 7 days by days 8-14"
    else:
        head = "No clear rise or fall expected over the next two weeks"
    if turn:
        head += f", with the shift from {turn['date']}"
    return {"headline": head, "baseline_last7_gbp_mwh": round(baseline, 1), "week1_mean": round(wk1, 1), "week2_mean": round(wk2, 1),
            "week1_vs_baseline_pct": round(pct1, 1), "week2_vs_baseline_pct": round(pct2, 1), "turning_point": turn,
            "cheapest_day": days[cheap]["date"], "dearest_day": days[dear]["date"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="uk-forecast.json")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--asof", help="replay a past day offline (YYYY-MM-DD)")
    ap.add_argument("--offline", action="store_true", help="skip downloads, use cache + no live weather (needs --asof)")
    a = ap.parse_args()
    os.makedirs(a.logs, exist_ok=True)
    lock = open(os.path.join(a.cache, ".lock"), "w") if os.path.isdir(a.cache) else None
    if lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("another run is in progress")

    asof = bool(a.asof)
    D = pd.Timestamp(a.asof) if asof else pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    flags = {}
    if not a.offline and not asof:
        flags = refresh(a.cache)
    print(f"issue date {D.date()}", flush=True)

    ctx = Ctx(a.cache, D, asof)
    status = "ok"
    if not asof:
        try:
            ctx.live = live_weather()
            flags["weather_live"] = True
        except Exception as e:  # noqa
            flags["weather_live"] = False
            status = "degraded"
            note(f"live weather unavailable ({e}); day 2+ uses the weather forecast from the history file's newest hours only")
            ctx.live = None
    X = ctx.rows()

    df = bt.build(a.cache, "A")
    model, ntrain = train(df, D)
    gate_ok, gate = health_gate(df, D)
    X["pred"] = model.predict(X[bt.feats()]) + X["sh7"]
    if not gate_ok:
        status = "degraded"
        note("health gate failed: model more than 15% worse than naive over the last 6 weeks; publishing naive forecast")
        X["pred"] = X["sh7"]

    # published day-ahead prices replace the forecast
    px = ctx.px_all
    px_pub = px[px.index.tz_convert(TZ).tz_localize(None) < D + pd.Timedelta(days=1)] if asof else px
    published = {}
    for T, g in X.groupby("target"):
        act = px_pub.reindex(g.index)
        if act.notna().sum() >= len(g) - 1 and len(g) > 0:
            published[T] = act
    pub_days = {T for T in published}
    scores = score_logs(a.logs, px)
    bands = band_tables(a.cache, scores)
    for h, b in bands.items():                            # bias correction only from logged evidence
        if b.get("source") == "logged" and abs(b.get("bias", 0)) > 0:
            X.loc[X["h"] == h, "pred"] -= b["bias"]

    days, hours, p50, pub_flags = [], [], [], []
    for h in range(1, HORIZON + 1):
        T = D + pd.Timedelta(days=h)
        g = X[X["target"] == T]
        vals = g["pred"].values.astype(float)
        isp = T in pub_days
        if isp:
            vals = published[T].values.astype(float)
        b = bands.get(h)
        mean = float(np.mean(vals))
        ent = {"date": T.strftime("%Y-%m-%d"), "h": h, "mean": round(mean, 1), "published": isp}
        if b and not isp:
            ent["p10"], ent["p90"] = round(mean - b["daily"][2], 1), round(mean - b["daily"][0], 1)
            ent["band_source"] = b["source"]
        days.append(ent)
        hours += [t.strftime("%Y-%m-%dT%H:%MZ") for t in g.index]
        p50 += [round(float(v), 1) for v in vals]
        pub_flags += [isp] * len(vals)

    # log today's forecast (unpublished days only are scored later)
    log = {"issue": str(D.date()), "level7": float(ctx.level7), "days": {}}
    for h in range(1, HORIZON + 1):
        T = D + pd.Timedelta(days=h)
        g = X[X["target"] == T]
        log["days"][str(h)] = {"published": T in pub_days, "hours_utc": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in g.index],
                               "p50": [round(float(v), 2) for v in g["pred"].values]}
    if not asof:
        json.dump(log, open(os.path.join(a.logs, f"forecast_{D.date()}.json"), "w"))

    acc = []
    for h in range(1, HORIZON + 1):
        s = scores.get(h)
        if s and s["n_days"] >= 30:
            acc.append({"h": h, "source": "logged", "n_days": s["n_days"], "daily_mae_pct": round(s["daily_mae_pct"], 1),
                        "trend_hit_pct": None if s["trend_hit"] is None else round(s["trend_hit"])})
    bt_res = os.path.join(a.cache, "backtest_results.json")
    backtest_acc = []
    if os.path.exists(bt_res):
        R = json.load(open(bt_res))
        for rA, rB in zip(R.get("A", []), R.get("B", [])):
            hh = rA["h"]
            backtest_acc.append({"h": hh, "daily_mae_pct": round((rA["daily_mae_pct"] + (rB["daily_mae_pct"] if hh > 7 else rA["daily_mae_pct"])) / (2 if hh > 7 else 1), 1),
                                 "trend_hit_pct": round((rA["trend_hit"] + (rB["trend_hit"] if hh > 7 else rA["trend_hit"])) / (2 if hh > 7 else 1))})
    try:
        regions = fit_regions(a.cache, px) if not (asof or a.offline) else (json.load(open(os.path.join(a.cache, "regions.json"))) if os.path.exists(os.path.join(a.cache, "regions.json")) else {})
    except Exception as e:  # noqa
        regions = {}
        note(f"region fit failed ({e})")
        status = "degraded"

    neso_w = None
    wp = os.path.join(a.cache, "wind_14d_now.csv")
    if os.path.exists(wp) and not asof:
        w = pd.read_csv(wp)
        w["Date"] = pd.to_datetime(w["Date"])
        neso_w = {d.strftime("%Y-%m-%d"): round(float(v) / 1000, 2) for d, v in w.groupby("Date")["Wind_Forecast"].mean().items()}

    out = {
        "version": VERSION, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "issue_date": str(D.date()),
        "timezone": "Europe/London", "status": status, "notes": NOTES,
        "units": {"wholesale": "GBP/MWh (N2EX GB day-ahead, hourly)", "agile": "p/kWh inc VAT"},
        "formula": "agile_p = min(cap, m * wholesale/10 + adder * (16:00<=UK local time<19:00) + c)  per half-hour; wholesale hour applies to both half-hours",
        "inputs": {"n2ex_latest_utc": px.index.max().strftime("%Y-%m-%dT%H:%MZ"), "sap_p_per_kwh": getattr(ctx, "sap_latest", None),
                   "level7_gbp_mwh": round(float(ctx.level7), 1), "train_rows": ntrain, "gate": gate, "flags": flags,
                   "neso_wind_14d_gw_mean": neso_w},
        "wholesale": {"start_utc": hours[0], "step_minutes": 60, "hours_utc": hours, "p50": p50, "published": pub_flags},
        "days": days,
        "bands": {str(h): {"hourly_p10": round(-b["hourly"][2], 1), "hourly_p90": round(-b["hourly"][0], 1),
                           "source": b["source"]} for h, b in bands.items()},
        "regions": regions,
        "summary": build_summary(days, float(ctx.level7)),
        "accuracy": {"logged": acc, "backtest": backtest_acc,
                     "note": "backtest = walk-forward on past data; days 8-14 are the average of an optimistic and a no-skill weather assumption. 'logged' replaces it as real forecasts accumulate."},
    }
    path = a.out
    atomic_write(path, json.dumps(out, separators=(",", ":"), default=lambda o: None if o != o else float(o)))
    print(f"wrote {path} ({os.path.getsize(path)/1024:.0f} kB); status {status}")
    print(out["summary"]["headline"])


if __name__ == "__main__":
    main()
