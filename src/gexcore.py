"""VENDORED COPY of mq-style-options-charts/scripts/mq_data.py (kept identical so the repo is self-contained).

Data layer for MenthorQ-style charts: option chain -> GEX/DEX by strike and by expiry, levels, proxy models.

CONVENTIONS (verified against the 26 Jun 2026 MenthorQ table, see references/style_guide.md section 4):
  GEX per contract = sign * gamma * OI * 100 * S^2 * 0.01   (calls +, puts -), dollars per 1% move
  DEX per contract = delta * OI * 100 * S                   (put delta negative), dollars
  GEX Normalized  = expiry GEX / sum(|expiry GEX|) * 100     (verified, max error 0.05 pp)
  OI Normalized   = expiry OI / total OI * 100
  DTE             = weekdays between asof date and expiry (holidays NOT excluded; verified 55/55)
  Expiry Exp.Move = S * sigma * sqrt(DTE/252) with ONE constant sigma (verified: 75.2*sqrt(DTE), sigma = 16.3%)
  Call Resistance = strike with the largest positive net GEX; Put Support = strike with the largest negative net GEX
  HVL             = strike where net GEX by strike changes sign (consistent with the screenshots; method not published)

Everything here is a MODEL built from open interest. It is not observed dealer positioning.
The fetch_* helpers must run on the user's machine (the Claude sandbox cannot reach these hosts) and were
NOT tested against the live feeds.
"""
import json
import math
import re
import urllib.request
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    from scipy.special import ndtr as _ndtr
except Exception:  # scipy is optional
    _erf = np.frompyfunc(math.erf, 1, 1)

    def _ndtr(x):
        return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)).astype(float))

OPT_RE = re.compile(r"^([A-Z]+W?)(\d{6})([CP])(\d{8})$")
T_FLOOR = (30 * 60) / (365 * 24 * 3600)


# ------------------------------------------------------------------ Black-Scholes (r = q = 0)
def bs_gamma_delta(S, K, sigma, T, is_call):
    S = np.asarray(S, float); K = np.asarray(K, float); sigma = np.asarray(sigma, float); T = np.asarray(T, float)
    sq = np.maximum(sigma * np.sqrt(T), 1e-12)
    d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / sq
    gamma = np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (S * sq)
    nd1 = _ndtr(d1)
    delta = np.where(is_call, nd1, nd1 - 1.0)
    return gamma, delta


# ------------------------------------------------------------------ chain loading
def chain_from_cboe_json(raw, asof=None):
    """Cboe delayed-quotes JSON (dict or path) -> (DataFrame, spot, asof). DataFrame columns:
    expiry, strike, cp, oi, iv, volume."""
    if isinstance(raw, str):
        with open(raw, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    data = raw.get("data", raw)
    spot = float(data.get("current_price") or data.get("close") or 0) or None
    ts = raw.get("timestamp") or data.get("timestamp")
    if asof is None and ts:
        asof = pd.to_datetime(ts)
    asof = pd.Timestamp(asof) if asof is not None else pd.Timestamp.now()
    rows = []
    for o in data.get("options", []):
        m = OPT_RE.match(o.get("option", ""))
        if not m:
            continue
        rows.append((pd.Timestamp(datetime.strptime(m.group(2), "%y%m%d")), int(m.group(4)) / 1000.0, m.group(3),
                     float(o.get("open_interest") or 0), float(o.get("iv") or 0), float(o.get("volume") or 0)))
    df = pd.DataFrame(rows, columns=["expiry", "strike", "cp", "oi", "iv", "volume"])
    df = df[(df.oi > 0) & (df.iv > 0)].reset_index(drop=True)
    return df, spot, asof


def fetch_cboe_chain(symbol="SPX", timeout=60):
    """RUN ON THE USER'S MACHINE. Indices need an underscore (_SPX, _NDX). Not tested on the live feed."""
    sym = "_" + symbol if symbol.upper() in {"SPX", "NDX", "RUT", "VIX", "XSP"} else symbol
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def add_greeks(df, spot, asof):
    """Adds T, gamma, delta, gex ($ per 1%), dex ($) columns. Contracts expiring at/before asof are dropped."""
    d = df.copy()
    exp_dt = d["expiry"] + pd.Timedelta(hours=16)
    d = d[exp_dt > asof].copy()
    exp_dt = d["expiry"] + pd.Timedelta(hours=16)
    d["T"] = np.maximum((exp_dt - asof).dt.total_seconds() / (365 * 24 * 3600), T_FLOOR)
    is_call = (d["cp"] == "C").to_numpy()
    g, dl = bs_gamma_delta(spot, d["strike"].to_numpy(), d["iv"].to_numpy(), d["T"].to_numpy(), is_call)
    sign = np.where(is_call, 1.0, -1.0)
    d["gamma"], d["delta"] = g, dl
    d["gex"] = sign * g * d["oi"] * 100 * spot ** 2 * 0.01
    d["dex"] = dl * d["oi"] * 100 * spot
    return d.reset_index(drop=True)


# ------------------------------------------------------------------ aggregation and levels
def by_strike(d, lo=None, hi=None):
    g = d.groupby("strike").agg(net_gex=("gex", "sum"), net_dex=("dex", "sum"), oi=("oi", "sum")).reset_index()
    cg = d[d.cp == "C"].groupby("strike")["gex"].sum().rename("call_gex")
    pg = d[d.cp == "P"].groupby("strike")["gex"].sum().rename("put_gex")
    g = g.merge(cg, on="strike", how="left").merge(pg, on="strike", how="left").fillna(0.0)
    if lo is not None:
        g = g[g.strike >= lo]
    if hi is not None:
        g = g[g.strike <= hi]
    return g.sort_values("strike").reset_index(drop=True)


def gex_profile(d, spot, grid_pct=(-0.06, 0.12), n=121):
    """Net GEX if spot were S' (IV and OI fixed). Returns (grid, total_gex)."""
    grid = spot * (1 + np.linspace(grid_pct[0], grid_pct[1], n))
    K = d["strike"].to_numpy()[None, :]; sig = d["iv"].to_numpy()[None, :]; T = d["T"].to_numpy()[None, :]
    oi = d["oi"].to_numpy()[None, :]; sign = np.where(d["cp"].to_numpy() == "C", 1.0, -1.0)[None, :]
    S = grid[:, None]
    g, _ = bs_gamma_delta(S, K, sig, T, True)
    tot = (sign * g * oi * 100 * S ** 2 * 0.01).sum(axis=1)
    return grid, tot


def levels(bs, d=None, spot=None, method="strike_sign_change"):
    """call_res / put_sup / hvl from a by_strike frame. hvl: strike-level sign change (default) or sweep flip."""
    if bs.empty:
        return {"call_res": None, "put_sup": None, "hvl": None}
    call_res = float(bs.loc[bs.net_gex.idxmax(), "strike"]) if bs.net_gex.max() > 0 else None
    put_sup = float(bs.loc[bs.net_gex.idxmin(), "strike"]) if bs.net_gex.min() < 0 else None
    hvl = None
    s = bs.strike.to_numpy(); v = bs.net_gex.to_numpy()
    if method == "sweep" and d is not None and spot is not None:
        grid, tot = gex_profile(d, spot, grid_pct=(-0.15, 0.15), n=301)
        idx = np.where(np.diff(np.sign(tot)) != 0)[0]
        if len(idx):
            i = idx[np.argmin(np.abs(grid[idx] - spot))]
            hvl = float(grid[i] - tot[i] * (grid[i + 1] - grid[i]) / (tot[i + 1] - tot[i]))
    else:
        sg = np.sign(v)
        idx = np.where((sg[:-1] < 0) & (sg[1:] > 0))[0]          # negative below, positive above
        if len(idx) and spot is not None:
            i = idx[np.argmin(np.abs(s[idx] - spot))]
            hvl = float(s[i + 1])
        elif len(idx):
            hvl = float(s[idx[0] + 1])
    return {"call_res": call_res, "put_sup": put_sup, "hvl": hvl}


def dte_weekdays(asof, expiry):
    return int(np.busday_count(np.datetime64(pd.Timestamp(asof).date()), np.datetime64(pd.Timestamp(expiry).date())))


def expiry_table(d, spot, asof, em_iv, prev=None):
    """Table like MenthorQ 'GEX by expiration'. prev = previous day's table (same columns) for the 1D change columns.
    em_iv: ONE constant annual vol (e.g. VIX/100 or 30d ATM IV) used for Expiry Exp. Move."""
    tot_oi = d["oi"].sum()
    rows = []
    for exp, g in d.groupby("expiry"):
        bs = by_strike(g)
        lv = levels(bs, g, spot)
        dte = dte_weekdays(asof, exp)
        rows.append({"expiry": exp, "DTE": dte, "GEX": g.gex.sum(), "DEX": g.dex.sum(), "OI": g.oi.sum(),
                     "Call Resistance": lv["call_res"], "Put Support": lv["put_sup"], "High Vol. Level": lv["hvl"],
                     "Expiry Exp. Move": spot * em_iv * math.sqrt(max(dte, 0) / 252.0) if dte > 0 else np.nan})
    t = pd.DataFrame(rows).sort_values("expiry").reset_index(drop=True)
    t["GEX Normalized"] = t.GEX / t.GEX.abs().sum() * 100
    t["DEX Normalized"] = t.DEX / t.DEX.abs().sum() * 100
    t["OI Normalized"] = t.OI / tot_oi * 100
    bs_all = by_strike(d)
    lv = levels(bs_all, d, spot)
    tot = {"expiry": "Tot. Exposure", "DTE": np.nan, "GEX": t.GEX.sum(), "DEX": t.DEX.sum(), "OI": tot_oi,
           "GEX Normalized": 100.0 * np.sign(t.GEX.sum()), "DEX Normalized": 100.0 * np.sign(t.DEX.sum()),
           "OI Normalized": 100.0, "Call Resistance": lv["call_res"], "Put Support": lv["put_sup"],
           "High Vol. Level": lv["hvl"], "Expiry Exp. Move": np.nan}
    t = pd.concat([pd.DataFrame([tot]), t], ignore_index=True)
    t["GEX Change 1D"] = np.nan; t["DEX Change 1D"] = np.nan
    if prev is not None:
        p = prev.set_index("expiry")
        for i, r in t.iterrows():
            if r["expiry"] in p.index:
                t.loc[i, "GEX Change 1D"] = r.GEX - p.loc[r["expiry"], "GEX"]
                t.loc[i, "DEX Change 1D"] = r.DEX - p.loc[r["expiry"], "DEX"]
    return t


# ------------------------------------------------------------------ skew (25 delta risk reversal, own definition)
def risk_reversal_25d(d, target_days=30):
    """(IV 25d put - IV 25d call) / IV ATM * 100 for the expiry closest to target_days. OWN DEFINITION: MenthorQ's
    exact formula is not published; their series moves in the 20-50 range, which this normalisation also gives."""
    d = d.copy()
    d["days"] = d["T"] * 365
    exp = d.iloc[(d["days"] - target_days).abs().argsort()[:1]]["expiry"].iloc[0]
    g = d[d.expiry == exp]
    c = g[g.cp == "C"].sort_values("strike"); p = g[g.cp == "P"].sort_values("strike")
    if len(c) < 3 or len(p) < 3:
        return np.nan
    iv_c = np.interp(0.25, c["delta"].to_numpy()[::-1], c["iv"].to_numpy()[::-1])
    iv_p = np.interp(0.25, (-p["delta"]).to_numpy(), p["iv"].to_numpy())
    atm = np.interp(0.5, c["delta"].to_numpy()[::-1], c["iv"].to_numpy()[::-1])
    return float((iv_p - iv_c) / atm * 100) if atm else np.nan


# ------------------------------------------------------------------ synthetic data (tests and demos only)
def synth_chain(spot=7338.0, asof="2026-06-26 16:14", seed=7, peak_m=73.0, atm_iv=0.163):
    """Plausible random chain, NOT real data. OI is rescaled so the largest |net GEX| strike is about peak_m millions."""
    rng = np.random.default_rng(seed)
    asof = pd.Timestamp(asof)
    exps = list(pd.bdate_range(asof.normalize() + pd.Timedelta(days=1), periods=45))
    exps += [pd.Timestamp(x) for x in ["2026-09-18", "2026-10-16", "2026-11-20", "2026-12-18", "2026-12-31",
                                       "2027-03-19", "2027-06-17", "2027-12-17", "2028-12-15", "2029-12-21"]]
    strikes = np.unique(np.r_[np.arange(spot * 0.80, spot * 1.25, 25), np.arange(spot * 0.93, spot * 1.08, 5)])
    strikes = np.round(strikes / 5) * 5
    rows = []
    for e in exps:
        w = 1.0 / math.sqrt(max((e - asof).days, 1) + 5)
        for K in strikes:
            m = math.log(K / spot)
            iv = atm_iv * (1 - 1.4 * m + 6 * m * m) * (1 + 0.1 * math.exp(-(e - asof).days / 200))
            near = math.exp(-0.5 * (m / 0.045) ** 2)
            round_bump = 2.0 if K % 100 == 0 else (1.4 if K % 50 == 0 else 1.0)
            coi = 4000 * w * round_bump * math.exp(-0.5 * ((m - 0.04) / 0.03) ** 2) * rng.uniform(0.5, 1.5) * (0.4 + near)
            poi = 5000 * w * round_bump * math.exp(-0.5 * ((m + 0.03) / 0.035) ** 2) * rng.uniform(0.5, 1.5) * (0.4 + near)
            rows.append((e, K, "C", coi, iv, 0.0)); rows.append((e, K, "P", poi, iv * 1.03, 0.0))
    df = pd.DataFrame(rows, columns=["expiry", "strike", "cp", "oi", "iv", "volume"])
    df = df[df.oi > 5].reset_index(drop=True)
    d = add_greeks(df, spot, asof)
    peak = by_strike(d).net_gex.abs().max() / 1e6
    df["oi"] = df["oi"] * (peak_m / peak)
    return df, spot, asof


def synth_ohlc(end="2026-06-26", n=120, start_price=6400.0, drift=0.0012, vol=0.009, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    r = rng.normal(drift, vol, n)
    close = start_price * np.exp(np.cumsum(r))
    open_ = np.r_[start_price, close[:-1]] * (1 + rng.normal(0, 0.002, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close}, index=idx)


# ------------------------------------------------------------------ price history loaders
def load_ohlc_csv(path):
    """CSV with date, open, high, low, close (any case; TradingView/Stooq/Yahoo exports work)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    dc = next(c for c in df.columns if c in ("date", "time", "datetime"))
    df[dc] = pd.to_datetime(df[dc], utc=False, errors="coerce")
    df = df.dropna(subset=[dc]).set_index(dc).sort_index()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df[["open", "high", "low", "close"]].astype(float)


def fetch_stooq(symbol="^spx"):
    """RUN LOCALLY. Daily OHLC from Stooq's CSV endpoint. Not tested here."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode("utf-8")
    path = "/tmp/_stooq.csv"
    open(path, "w").write(txt)
    return load_ohlc_csv(path)


def fetch_yahoo(symbol="^GSPC", period="2y"):
    """RUN LOCALLY, needs `pip install yfinance`. Not tested here."""
    import yfinance as yf
    df = yf.download(symbol, period=period, auto_adjust=False, progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    return df[["open", "high", "low", "close"]].astype(float)


def fetch_squeezemetrics():
    """RUN LOCALLY. SqueezeMetrics (a competitor) publishes a free daily DIX/GEX history CSV; its GEX is a different
    model from ours. URL seen in public tooling, not verified by me."""
    url = "https://squeezemetrics.com/monitor/static/DIX.csv"
    return pd.read_csv(url, parse_dates=["date"]).set_index("date")


def fetch_cboe_vix_history():
    """RUN LOCALLY. Official Cboe daily VIX OHLC CSV (URL seen in public tooling, not verified by me)."""
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    return pd.read_csv(url, parse_dates=["DATE"]).set_index("DATE")


# ------------------------------------------------------------------ OWN proxy models (not MenthorQ's models)
def realized_vol(close, window):
    return np.log(close).diff().rolling(window).std() * math.sqrt(252)


def vol_control_frame(close):
    rv1, rv4 = realized_vol(close, 21), realized_vol(close, 84)
    out = pd.DataFrame({"rv1m": rv1, "rv4m": rv4})
    out["diff"] = out.rv4m - out.rv1m           # histogram: red when short-term vol exceeds long-term vol (inferred)
    out["ratio"] = out.rv1m / out.rv4m
    out["ratio_mean10"] = out["ratio"].rolling(10).mean()
    return out


def momentum_score(close):
    """0-5: close above SMA20/50/200, RSI14 above 50, 20d return positive. OWN rule."""
    c = close
    delta = c.diff(); up = delta.clip(lower=0).rolling(14).mean(); dn = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    s = ((c > c.rolling(20).mean()).astype(int) + (c > c.rolling(50).mean()).astype(int)
         + (c > c.rolling(200, min_periods=60).mean()).astype(int) + (rsi > 50).astype(int)
         + (c.pct_change(20) > 0).astype(int))
    return s.astype(float)


def volatility_score(close, window=252):
    """1-5 from the percentile of 21d realized vol within the last `window` sessions. OWN rule."""
    rv = realized_vol(close, 21)
    pct = rv.rolling(window, min_periods=40).apply(lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
    return np.clip(np.ceil(pct * 5), 1, 5)


def seasonality_score(close, horizon=5, min_years=5):
    """-5..5: mean forward `horizon`-day return of the same calendar day in earlier years, as a z-score. OWN rule;
    needs several years of history."""
    fwd = close.shift(-horizon) / close - 1
    z = pd.Series(np.nan, index=close.index)
    allstd = fwd.std()
    for dt in close.index:
        past = fwd[(fwd.index.year < dt.year) & (abs(fwd.index.dayofyear - dt.dayofyear) <= 2)].dropna()
        if past.index.year.nunique() >= min_years:
            z[dt] = past.mean() / (allstd / math.sqrt(max(past.index.year.nunique(), 1)))
    return z.clip(-5, 5)


def score_from_series(x, window=60):
    """Turn any series (e.g. logged net GEX) into a 0-5 score by rolling percentile. OWN rule."""
    pct = x.rolling(window, min_periods=10).apply(lambda v: (v[:-1] < v[-1]).mean() if len(v) > 1 else np.nan, raw=True)
    return np.round(pct * 5)


def cta_proxy(close, lookbacks=(21, 63, 126, 252), target_vol=0.10, cap=1.5):
    """Trend-following exposure in [-cap, cap]: mean sign of returns over the lookbacks, scaled by target_vol / rv21.
    A PROXY for the idea of CTA positioning, not MenthorQ's Q-CTA and not an estimate of real CTA flows."""
    sig = sum(np.sign(close.pct_change(L)) for L in lookbacks) / len(lookbacks)
    scale = (target_vol / realized_vol(close, 21)).clip(upper=cap)
    return (sig * scale).dropna()


def cta_stats_table(positions: dict):
    """positions: {name: Series of daily position}. Returns the 7 columns of the MenthorQ CTA table."""
    rows = {}
    for name, s in positions.items():
        s = s.dropna()
        if len(s) < 30:
            continue
        pr = lambda n: float((s.iloc[-n:] <= s.iloc[-1]).mean())
        w = s.iloc[-63:]
        rows[name] = {"CTA Position Today": s.iloc[-1], "CTA Position Yesterday": s.iloc[-2],
                      "CTA Position 1 Month Ago": s.iloc[-22] if len(s) > 22 else np.nan,
                      "1M Percentile": pr(21), "3M Percentile": pr(63), "1Y Percentile": pr(252),
                      "3M Z Score": float((s.iloc[-1] - w.mean()) / (w.std() or np.nan))}
    return pd.DataFrame(rows).T
