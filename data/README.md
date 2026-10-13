# data/

Everything in this folder is written by `src/collect.py` (GitHub Actions). Do not edit by hand.

| Path | Content |
|---|---|
| `YYYY-MM-DD/<SYMBOL>.json.gz` | full profile of that session (gzip, compact JSON, schema 1) |
| `latest/<SYMBOL>.json` | the newest profile, plain JSON |
| `history/<SYMBOL>_daily.csv` | one summary row per session |
| `index.json` | which symbols and dates exist, and when new data was last written |

## Profile (schema 1)

```
schema, symbol, trade_date (YYYY-MM-DD), asof (ET, from the Cboe timestamp), collected_at_utc, source,
spot, em_iv (30d ATM implied vol used for "Expiry Exp. Move"),
totals     {gex, dex, oi, contracts, expiries}            dollars per 1% move (GEX), dollars (DEX)
levels     {call_res, put_sup, hvl, hvl_sweep}            strike levels; hvl_sweep = spot-sweep flip, may be null
skew       {rr25_30d, atm_iv_30d}                         (IV 25d put - IV 25d call) / IV ATM * 100, own definition
by_strike  {cols:[strike, net_gex, call_gex, put_gex, net_dex, oi], rows:[...]}   display window around spot
profile    {gex:{price:[...], value:[...]}}               net GEX if spot moved to `price` (IV and OI fixed)
expiry_table {cols:[expiry, dte, gex, dex, oi, gex_norm, dex_norm, oi_norm, gex_chg_1d, dex_chg_1d,
              call_res, put_sup, hvl, exp_move], rows:[...]}   first row is TOTAL
panels     [{key: first|next|hi|hi2, expiry, gex_expiring_pct, levels, by_strike, profile}]
quality    {warnings:[...]}
```

Conventions: calls positive, puts negative; gamma/delta from Black-Scholes (r = q = 0) with the IV in the payload;
open interest is the previous-day figure Cboe shows; contracts expiring before the snapshot time are excluded;
`DTE` counts weekdays. These are MODELLED values, not observed dealer positions.
