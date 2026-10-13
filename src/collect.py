#!/usr/bin/env python3
"""Daily collector: Cboe delayed option chain -> per-strike GEX profile files committed to the repository.

    python src/collect.py                     # all symbols from config.json, normal daily run
    python src/collect.py --symbols SPX       # only some
    python src/collect.py --force             # run on a closed day / accept a stale payload (testing only)
    python src/collect.py --allow-intraday    # accept a snapshot taken before 16:30 ET (marked in quality.warnings)
    python src/collect.py --fixture-dir DIR   # offline: read DIR/<cboe code>.json instead of the network
    python src/collect.py --now 2026-10-09T18:45:00   # pretend it is this ET time (tests)

Output (under --out, default data/):
    YYYY-MM-DD/<SYMBOL>.json.gz   full profile (schema 1, see src/schema.py), deterministic bytes (rerun = no git diff)
    latest/<SYMBOL>.json          copy of the newest profile, plain JSON
    history/<SYMBOL>_daily.csv    one summary row per session (upserted by trade_date)
    index.json                    what exists, for consumers that cannot list directories

Exit code 0 = everything fine (including 'market closed' and 'already collected'); 1 = at least one symbol failed.
A failed symbol never blocks the others and never leaves a half-written file.
"""
import argparse
import csv
import gzip
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gexcore as G  # noqa: E402
from schema import SCHEMA_VERSION, BY_STRIKE_COLS, EXPIRY_COLS, validate_profile  # noqa: E402
from us_calendar import is_trading_day, last_finished_session  # noqa: E402

ET = ZoneInfo("America/New_York")
HISTORY_COLS = ["trade_date", "asof", "spot", "net_gex", "net_dex", "call_res", "put_sup", "hvl", "hvl_sweep", "rr25_30d",
                "atm_iv_30d", "oi_total", "contracts", "open", "high", "low", "close", "source"]
EXPIRY_MAP = {"expiry": "expiry", "dte": "DTE", "gex": "GEX", "dex": "DEX", "oi": "OI", "gex_norm": "GEX Normalized",
              "dex_norm": "DEX Normalized", "oi_norm": "OI Normalized", "gex_chg_1d": "GEX Change 1D",
              "dex_chg_1d": "DEX Change 1D", "call_res": "Call Resistance", "put_sup": "Put Support", "hvl": "High Vol. Level",
              "exp_move": "Expiry Exp. Move"}


class CollectError(Exception):
    """A problem with one symbol: it is reported and skipped, the others continue."""


# ------------------------------------------------------------------ small helpers
def clean(x, nd=None):
    """JSON-safe number: NaN/inf -> None, optional rounding (nd=0 -> int)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if nd is None:
        return x
    return int(round(x)) if nd == 0 else round(x, nd)


def now_et(override=None):
    if override:
        return datetime.fromisoformat(override).replace(tzinfo=ET)
    return datetime.now(ET)


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fetch_chain(code, retries=4, timeout=60, base_wait=5):
    """Download the Cboe delayed-quotes JSON. Retries with growing waits; raises CollectError if all fail."""
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{code}.json"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; gex-data-collector)",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if len(body) < 100_000:
                raise CollectError(f"response too small ({len(body)} bytes): not a full chain")
            return json.loads(body.decode("utf-8"))
        except Exception as e:  # network errors, HTTP errors, JSON errors
            last = e
            if i < retries - 1:
                time.sleep(base_wait * (3 ** i))
    raise CollectError(f"download failed after {retries} tries: {last}")


def load_raw(code, fixture_dir):
    if fixture_dir:
        p = os.path.join(fixture_dir, f"{code}.json")
        if not os.path.exists(p):
            raise CollectError(f"fixture {p} not found")
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return fetch_chain(code)


def atm_iv_30d(d, target_days=30):
    """ATM implied vol of the expiry closest to `target_days` (call with delta nearest 0.5). None if impossible."""
    d = d.copy()
    d["days"] = d["T"] * 365
    if d.empty:
        return None
    exp = d.iloc[(d["days"] - target_days).abs().argsort()[:1]]["expiry"].iloc[0]
    c = d[(d.expiry == exp) & (d.cp == "C")].sort_values("delta")
    if len(c) < 3:
        return None
    return float(np.interp(0.5, c["delta"].to_numpy(), c["iv"].to_numpy()))


def extract_ohlc(raw, spot):
    """Daily OHLC of the underlying if the payload carries it and it is sane; otherwise (None, reason). The field names
    are an assumption about the Cboe payload and are NOT verified: that is why every value is sanity-checked."""
    data = raw.get("data", raw)
    try:
        o, h, l, c = (float(data[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return None, "payload has no open/high/low/close"
    ok = 0 < l <= min(o, c) + 1e-9 and h >= max(o, c) - 1e-9 and abs(h / spot - 1) < 0.05 and abs(l / spot - 1) < 0.05
    return ((o, h, l, c), None) if ok else (None, f"open/high/low/close failed sanity checks ({o}, {h}, {l}, {c})")


def prev_profile_path(out, symbol, trade_date):
    days = sorted(x for x in os.listdir(out) if len(x) == 10 and x[4] == "-" and x < trade_date) if os.path.isdir(out) else []
    for day in reversed(days):
        p = os.path.join(out, day, f"{symbol}.json.gz")
        if os.path.exists(p):
            return p
    return None


def read_profile(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def prev_table_frame(prev):
    """Previous day's expiry_table as the DataFrame mq-style expiry_table() expects."""
    if not prev:
        return None
    t = prev["expiry_table"]
    rows = []
    for r in t["rows"]:
        x = dict(zip(t["cols"], r))
        rows.append({"expiry": "Tot. Exposure" if x["expiry"] == "TOTAL" else pd.Timestamp(x["expiry"]),
                     "GEX": x["gex"], "DEX": x["dex"]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ the profile
def by_strike_table(bs):
    f = bs
    return {"cols": BY_STRIKE_COLS, "rows": [[clean(r.strike, 2), clean(r.net_gex, 0), clean(r.call_gex, 0), clean(r.put_gex, 0),
                                              clean(r.net_dex, 0), clean(r.oi, 0)] for r in f.itertuples()]}


def levels_dict(lv, sweep=None):
    return {"call_res": clean(lv["call_res"], 2), "put_sup": clean(lv["put_sup"], 2), "hvl": clean(lv["hvl"], 2),
            "hvl_sweep": clean(sweep, 2)}


def profile_dict(grid, vals):
    return {"gex": {"price": [clean(x, 2) for x in grid], "value": [clean(x, 0) for x in vals]}}


def build_profile(raw, symbol, cfg, prev, collected_at_utc):
    scfg = cfg["symbols"][symbol]
    chain, spot, asof = G.chain_from_cboe_json(raw)
    warnings = []
    if not spot or spot <= 0:
        raise CollectError("payload has no spot price")
    if len(chain) < scfg["min_contracts"]:
        raise CollectError(f"only {len(chain)} usable contracts (< {scfg['min_contracts']}): incomplete chain")
    if chain.expiry.nunique() < 5:
        raise CollectError(f"only {chain.expiry.nunique()} expiries: incomplete chain")
    med_iv = float(chain.iv.median())
    if not 0.03 < med_iv < 2.5:
        raise CollectError(f"median IV {med_iv:.3f} outside 3%..250%: IV field suspect (percent vs fraction?)")
    d = G.add_greeks(chain, spot, asof)
    if len(d) < 0.5 * len(chain):
        raise CollectError("more than half of the contracts already expired at the snapshot time: wrong timestamp?")
    em_iv = atm_iv_30d(d)
    if em_iv is None or not 0.01 < em_iv < 3:
        warnings.append(f"atm_iv_30d unavailable, using default {cfg['default_em_iv']}")
        em_iv = cfg["default_em_iv"]
    lo, hi = spot * (1 + cfg["strike_window"][0]), spot * (1 + cfg["strike_window"][1])
    bs_all = G.by_strike(d)
    lv = G.levels(bs_all, d, spot)
    sweep = G.levels(bs_all, d, spot, method="sweep")["hvl"]
    bs_win = G.by_strike(d, lo, hi)
    if len(bs_win) < 10:
        raise CollectError("fewer than 10 strikes inside the display window")
    grid, tot = G.gex_profile(d, spot, tuple(cfg["strike_window"]), cfg["profile_points"])
    t = G.expiry_table(d, spot, asof, em_iv, prev=prev_table_frame(prev))
    t2 = t.copy()
    t2["expiry"] = t2["expiry"].map(lambda x: "TOTAL" if isinstance(x, str) else pd.Timestamp(x).strftime("%Y-%m-%d"))
    nd = [None, 0, 0, 0, 0, 3, 3, 3, 0, 0, 2, 2, 2, 1]          # rounding per EXPIRY_COLS column (None = keep as is)
    expiry_tbl = {"cols": EXPIRY_COLS, "rows": []}
    for _, r in t2.iterrows():
        expiry_tbl["rows"].append([r["expiry"]] + [clean(r[EXPIRY_MAP[c]], n) for c, n in zip(EXPIRY_COLS[1:], nd[1:])])
    ex = t.iloc[1:].copy()
    ex["absn"] = ex["GEX Normalized"].abs()
    picks = [("first", ex.iloc[0])]
    if len(ex) > 1:
        picks.append(("next", ex.iloc[1]))
    rest = ex.iloc[2:].sort_values("absn", ascending=False)
    for key, (_, r) in zip(("hi", "hi2"), rest.head(2).iterrows()):
        picks.append((key, r))
    panels = []
    for key, r in picks:
        g = d[d.expiry == r["expiry"]]
        b = G.by_strike(g, lo, hi)
        pg, pv = G.gex_profile(g, spot, tuple(cfg["strike_window"]), cfg["panel_profile_points"])
        panels.append({"key": key, "expiry": pd.Timestamp(r["expiry"]).strftime("%Y-%m-%d"),
                       "gex_expiring_pct": clean(r["GEX Normalized"], 3), "levels": levels_dict(G.levels(G.by_strike(g), g, spot)),
                       "by_strike": by_strike_table(b), "profile": profile_dict(pg, pv)})
    rr = G.risk_reversal_25d(d, 30)
    ohlc, why = extract_ohlc(raw, spot)
    if why:
        warnings.append(why)
    trade_date = asof.strftime("%Y-%m-%d")
    prof = {"schema": SCHEMA_VERSION, "symbol": symbol, "trade_date": trade_date, "asof": asof.strftime("%Y-%m-%dT%H:%M:%S"),
            "collected_at_utc": collected_at_utc, "source": "Cboe delayed quotes (modelled GEX, calls +, puts -)",
            "spot": clean(spot, 2), "em_iv": clean(em_iv, 4),
            "totals": {"gex": clean(d.gex.sum(), 0), "dex": clean(d.dex.sum(), 0), "oi": clean(d.oi.sum(), 0),
                       "contracts": int(len(d)), "expiries": int(d.expiry.nunique())},
            "levels": levels_dict(lv, sweep), "skew": {"rr25_30d": clean(rr, 3), "atm_iv_30d": clean(em_iv, 4)},
            "by_strike": by_strike_table(bs_win), "profile": profile_dict(grid, tot), "expiry_table": expiry_tbl,
            "panels": panels, "quality": {"warnings": warnings}}
    row = {"trade_date": trade_date, "asof": prof["asof"], "spot": prof["spot"], "net_gex": prof["totals"]["gex"],
           "net_dex": prof["totals"]["dex"], "call_res": prof["levels"]["call_res"], "put_sup": prof["levels"]["put_sup"],
           "hvl": prof["levels"]["hvl"], "hvl_sweep": prof["levels"]["hvl_sweep"], "rr25_30d": prof["skew"]["rr25_30d"],
           "atm_iv_30d": prof["skew"]["atm_iv_30d"], "oi_total": prof["totals"]["oi"], "contracts": prof["totals"]["contracts"],
           "open": ohlc[0] if ohlc else None, "high": ohlc[1] if ohlc else None, "low": ohlc[2] if ohlc else None,
           "close": ohlc[3] if ohlc else None, "source": "Cboe" if ohlc else ""}
    errs = validate_profile(prof)
    if errs:
        raise CollectError("profile failed validation: " + "; ".join(errs[:4]))
    return prof, row


# ------------------------------------------------------------------ writing
def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def write_atomic(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def gz_bytes(data: bytes):
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as g:   # mtime=0: identical input -> identical bytes
        g.write(data)
    return buf.getvalue()


def upsert_history(path, row):
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["trade_date"]] = r
    rows[row["trade_date"]] = {k: ("" if row.get(k) is None else row[k]) for k in HISTORY_COLS}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_COLS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in HISTORY_COLS})
    os.replace(tmp, path)


def rebuild_index(out, stamp, wrote):
    """index.json lists what exists. `updated_utc` moves only when new data was written, so an idle run changes nothing."""
    syms = {}
    for day in sorted(x for x in os.listdir(out) if len(x) == 10 and x[4] == "-"):
        for f in os.listdir(os.path.join(out, day)):
            if f.endswith(".json.gz"):
                syms.setdefault(f[:-8], []).append(day)
    new = {s: {"dates": v, "latest": v[-1]} for s, v in sorted(syms.items())}
    path = os.path.join(out, "index.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    if old and old.get("symbols") == new and not wrote:
        return
    idx = {"schema": SCHEMA_VERSION, "updated_utc": stamp if (wrote or not old) else old.get("updated_utc", stamp),
           "layout": {"profile": "<date>/<SYMBOL>.json.gz", "latest": "latest/<SYMBOL>.json", "history": "history/<SYMBOL>_daily.csv"},
           "symbols": new}
    write_atomic(path, json.dumps(idx, indent=1).encode("utf-8"))


def process_symbol(symbol, cfg, args, out, now):
    scfg = cfg["symbols"][symbol]
    raw = load_raw(scfg["cboe"], args.fixture_dir)
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # previous profile is needed before we know the date: peek at the payload timestamp first
    asof = pd.to_datetime(raw.get("timestamp") or raw.get("data", {}).get("timestamp"))
    if pd.isna(asof):
        raise CollectError("payload has no timestamp")
    trade_date = asof.strftime("%Y-%m-%d")
    expected = last_finished_session(now).isoformat()
    if not args.force:
        if asof.date() == now.date() and (asof.hour, asof.minute) < (16, 0) and not args.allow_intraday:
            raise CollectError(f"intraday snapshot ({asof}); rerun after the close or pass --allow-intraday")
        if trade_date != expected and not (args.allow_intraday and trade_date == now.date().isoformat()):
            raise CollectError(f"stale payload: timestamp {asof} but the latest finished session is {expected}")
    prev_path = prev_profile_path(out, symbol, trade_date)
    prev = read_profile(prev_path) if prev_path else None
    prof, row = build_profile(raw, symbol, cfg, prev, stamp)
    if args.allow_intraday and trade_date == now.date().isoformat() and (asof.hour, asof.minute) < (16, 0):
        prof["quality"]["warnings"].append("intraday snapshot")
    dated = os.path.join(out, trade_date, f"{symbol}.json.gz")
    if os.path.exists(dated):
        old = read_profile(dated)
        if str(old.get("asof", "")) >= prof["asof"]:
            return "unchanged", prof
    body = dumps(prof)
    write_atomic(dated, gz_bytes(body))
    write_atomic(os.path.join(out, "latest", f"{symbol}.json"), body)
    upsert_history(os.path.join(out, "history", f"{symbol}_daily.csv"), row)
    return "written", prof


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data"); ap.add_argument("--config", default=os.path.join(HERE, "..", "config.json"))
    ap.add_argument("--symbols"); ap.add_argument("--force", action="store_true"); ap.add_argument("--allow-intraday", action="store_true")
    ap.add_argument("--fixture-dir"); ap.add_argument("--now")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    now = now_et(args.now)
    if not args.force and not is_trading_day(now.date()):
        print(f"market closed on {now.date()} (weekend/holiday): nothing to do")
        return 0
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else list(cfg["symbols"])
    unknown = [s for s in symbols if s not in cfg["symbols"]]
    if unknown:
        print(f"unknown symbols {unknown}; known: {list(cfg['symbols'])}", file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)
    failed, results = [], []
    for s in symbols:
        try:
            status, prof = process_symbol(s, cfg, args, args.out, now)
            results.append((s, status, prof["trade_date"], prof["spot"], len(prof["by_strike"]["rows"])))
            print(f"OK   {s}: {status} {prof['trade_date']} spot={prof['spot']} strikes={len(prof['by_strike']['rows'])}"
                  + (f" warnings={prof['quality']['warnings']}" if prof["quality"]["warnings"] else ""))
        except CollectError as e:
            failed.append(s); print(f"FAIL {s}: {e}", file=sys.stderr)
        except Exception as e:  # a bug must not hide the other symbols
            failed.append(s); print(f"FAIL {s}: unexpected {type(e).__name__}: {e}", file=sys.stderr)
    rebuild_index(args.out, now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), any(r[1] == "written" for r in results))
    if failed:
        print(f"{len(failed)} of {len(symbols)} symbols failed: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
