"""Validation of the daily profile file (schema 1). Used by the collector before writing and by consumers after reading.

validate_profile(d) -> list of human-readable problems (empty list = valid). It never raises on bad input.
"""
import math

SCHEMA_VERSION = 1
BY_STRIKE_COLS = ["strike", "net_gex", "call_gex", "put_gex", "net_dex", "oi"]
EXPIRY_COLS = ["expiry", "dte", "gex", "dex", "oi", "gex_norm", "dex_norm", "oi_norm", "gex_chg_1d", "dex_chg_1d",
               "call_res", "put_sup", "hvl", "exp_move"]
TOP_KEYS = ["schema", "symbol", "trade_date", "asof", "collected_at_utc", "source", "spot", "em_iv", "totals", "levels",
            "skew", "by_strike", "profile", "expiry_table", "panels", "quality"]
LEVEL_KEYS = ["call_res", "put_sup", "hvl", "hvl_sweep"]


def _num(x, allow_none=False):
    if x is None:
        return allow_none
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _table(t, cols, name, errs, numeric_from=0, none_ok=()):
    if not isinstance(t, dict) or t.get("cols") != cols or not isinstance(t.get("rows"), list):
        errs.append(f"{name}: wrong columns or rows"); return
    for i, r in enumerate(t["rows"]):
        if not isinstance(r, list) or len(r) != len(cols):
            errs.append(f"{name}: row {i} has wrong length"); return
        for j, v in enumerate(r):
            if j < numeric_from:
                continue
            if not _num(v, allow_none=cols[j] in none_ok):
                errs.append(f"{name}: row {i} column {cols[j]} not a finite number ({v!r})"); return


def _profile(p, name, errs):
    g = (p or {}).get("gex") if isinstance(p, dict) else None
    if not isinstance(g, dict) or not isinstance(g.get("price"), list) or not isinstance(g.get("value"), list) \
            or len(g["price"]) != len(g["value"]) or len(g["price"]) < 5:
        errs.append(f"{name}: profile.gex needs equal-length price/value lists (>= 5 points)"); return
    if not all(_num(x) for x in g["price"] + g["value"]):
        errs.append(f"{name}: profile contains non-finite numbers")


def _levels(lv, name, errs):
    if not isinstance(lv, dict) or any(k not in lv for k in LEVEL_KEYS):
        errs.append(f"{name}: levels need keys {LEVEL_KEYS}"); return
    for k in LEVEL_KEYS:
        if not _num(lv[k], allow_none=True):
            errs.append(f"{name}: level {k} is not a number or null")


def validate_profile(d):
    errs = []
    if not isinstance(d, dict):
        return ["not an object"]
    for k in TOP_KEYS:
        if k not in d:
            errs.append(f"missing key {k}")
    if errs:
        return errs
    if d["schema"] != SCHEMA_VERSION:
        errs.append(f"schema {d['schema']!r} != {SCHEMA_VERSION}")
    if not isinstance(d["symbol"], str) or not d["symbol"]:
        errs.append("symbol")
    if not (isinstance(d["trade_date"], str) and len(d["trade_date"]) == 10 and d["trade_date"][4] == "-"):
        errs.append("trade_date must be YYYY-MM-DD")
    if not _num(d["spot"]) or d["spot"] <= 0:
        errs.append("spot must be a positive number")
    if not _num(d["em_iv"]) or not 0.01 < d["em_iv"] < 3:
        errs.append("em_iv outside 1%..300%")
    _levels(d["levels"], "levels", errs)
    _table(d["by_strike"], BY_STRIKE_COLS, "by_strike", errs)
    if not errs and len(d["by_strike"]["rows"]) < 10:
        errs.append("by_strike has fewer than 10 strikes")
    _table(d["expiry_table"], EXPIRY_COLS, "expiry_table", errs, numeric_from=1,
           none_ok=("dte", "gex_chg_1d", "dex_chg_1d", "call_res", "put_sup", "hvl", "exp_move"))
    _profile(d["profile"], "profile", errs)
    t = d["totals"]
    if not isinstance(t, dict) or not all(_num(t.get(k)) for k in ("gex", "dex", "oi", "contracts", "expiries")):
        errs.append("totals need numeric gex, dex, oi, contracts, expiries")
    if not isinstance(d["panels"], list) or not 1 <= len(d["panels"]) <= 4:
        errs.append("panels must hold 1-4 entries")
    else:
        for i, p in enumerate(d["panels"]):
            for k in ("key", "expiry", "gex_expiring_pct", "levels", "by_strike", "profile"):
                if k not in p:
                    errs.append(f"panels[{i}] missing {k}")
            if not any(e.startswith(f"panels[{i}]") for e in errs):
                _levels(p["levels"], f"panels[{i}].levels", errs)
                _table(p["by_strike"], BY_STRIKE_COLS, f"panels[{i}].by_strike", errs)
                _profile(p["profile"], f"panels[{i}].profile", errs)
    if not isinstance(d["quality"], dict) or not isinstance(d["quality"].get("warnings"), list):
        errs.append("quality.warnings must be a list")
    return errs
