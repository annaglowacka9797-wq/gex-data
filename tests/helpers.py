"""Test helpers: build Cboe-format JSON payloads from the synthetic chain generator (no network, nothing committed)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import gexcore as G  # noqa: E402


def cboe_payload(spot, asof, seed=7, ohlc=None, drop_to=None, iv_scale=1.0, root="SPXW"):
    chain, spot, asof_ts = G.synth_chain(spot=spot, asof=asof, seed=seed)
    if drop_to:
        chain = chain.head(drop_to)
    opts = []
    for r in chain.itertuples():
        opts.append({"option": f"{root}{r.expiry:%y%m%d}{r.cp}{int(round(r.strike * 1000)):08d}", "open_interest": float(r.oi),
                     "iv": float(r.iv) * iv_scale, "volume": 0.0})
    data = {"current_price": spot, "options": opts}
    if ohlc:
        data.update(dict(zip(("open", "high", "low", "close"), ohlc)))
    return {"timestamp": asof_ts.strftime("%Y-%m-%d %H:%M:%S"), "data": data}


def write_fixture(dirpath, code, payload):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, f"{code}.json"), "w") as fh:
        json.dump(payload, fh)
