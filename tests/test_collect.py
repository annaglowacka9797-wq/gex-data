import csv, gzip, io, json, os, shutil, sys, tempfile, unittest, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import collect as C
from schema import validate_profile
from helpers import cboe_payload, write_fixture

CFG = os.path.join(HERE, "..", "config.json")
NOW1 = "2026-10-09T18:45:00"       # Friday after the close (EDT)
NOW2 = "2026-10-12T18:45:00"       # next Monday


def run(args, capsys=False):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = C.main(args)
    return code, out.getvalue(), err.getvalue()


def rd(path, mode="r"):
    with open(path, mode) as fh:
        return fh.read()


def jl(path):
    return json.loads(rd(path))


def rows_of(path):
    return list(csv.DictReader(io.StringIO(rd(path))))


def load(path):
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.fx = os.path.join(self.tmp, "fx"); self.out = os.path.join(self.tmp, "data")
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 16:15", ohlc=(7786.36, 7820.57, 7779.34, 7811.54)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, now=NOW1, *extra):
        return run(["--out", self.out, "--config", CFG, "--symbols", "SPX", "--fixture-dir", self.fx, "--now", now, *extra])


class TestHappyPath(Base):
    def test_writes_all_outputs(self):
        code, so, se = self.go()
        self.assertEqual(code, 0, se)
        p = os.path.join(self.out, "2026-10-09", "SPX.json.gz")
        self.assertTrue(os.path.exists(p))
        prof = load(p)
        self.assertEqual(validate_profile(prof), [])
        self.assertEqual(prof["trade_date"], "2026-10-09"); self.assertEqual(prof["symbol"], "SPX")
        self.assertTrue(os.path.exists(os.path.join(self.out, "latest", "SPX.json")))
        self.assertTrue(os.path.exists(os.path.join(self.out, "history", "SPX_daily.csv")))
        idx = jl(os.path.join(self.out, "index.json"))
        self.assertEqual(idx["symbols"]["SPX"]["latest"], "2026-10-09")

    def test_latest_equals_dated(self):
        self.go()
        a = load(os.path.join(self.out, "2026-10-09", "SPX.json.gz")); b = jl(os.path.join(self.out, "latest", "SPX.json"))
        self.assertEqual(a, b)

    def test_levels_match_independent_computation(self):
        self.go()
        prof = load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))
        import gexcore as G
        chain, spot, asof = G.chain_from_cboe_json(jl(os.path.join(self.fx, "_SPX.json")))
        d = G.add_greeks(chain, spot, asof); bs = G.by_strike(d); lv = G.levels(bs, d, spot)
        self.assertEqual(prof["levels"]["call_res"], lv["call_res"]); self.assertEqual(prof["levels"]["put_sup"], lv["put_sup"])
        self.assertEqual(prof["levels"]["hvl"], lv["hvl"])
        self.assertAlmostEqual(prof["totals"]["gex"], d.gex.sum(), delta=1)

    def test_expiry_table_invariants(self):
        self.go()
        t = load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))["expiry_table"]
        rows = [dict(zip(t["cols"], r)) for r in t["rows"]]
        tot, body = rows[0], rows[1:]
        self.assertEqual(tot["expiry"], "TOTAL")
        self.assertAlmostEqual(sum(r["gex"] for r in body), tot["gex"], delta=len(body))       # rounding to dollars
        self.assertAlmostEqual(sum(abs(r["gex_norm"]) for r in body), 100, delta=0.2)
        self.assertAlmostEqual(sum(r["oi_norm"] for r in body), 100, delta=0.2)
        self.assertTrue(all(r["dte"] >= 1 for r in body))
        self.assertEqual([r["expiry"] for r in body], sorted(r["expiry"] for r in body))

    def test_panels_and_sizes(self):
        self.go()
        p = os.path.join(self.out, "2026-10-09", "SPX.json.gz")
        prof = load(p)
        self.assertEqual([x["key"] for x in prof["panels"]], ["first", "next", "hi", "hi2"])
        self.assertLess(os.path.getsize(p), 120_000, "compressed profile should stay small")
        self.assertLess(len(json.dumps(prof)), 400_000, "plain profile must fit the 256 kB database document limit after trimming")

    def test_history_row_and_ohlc(self):
        self.go()
        rows = rows_of(os.path.join(self.out, "history", "SPX_daily.csv"))
        self.assertEqual(len(rows), 1); r = rows[0]
        self.assertEqual(r["trade_date"], "2026-10-09"); self.assertEqual(float(r["close"]), 7811.54); self.assertEqual(r["source"], "Cboe")

    def test_no_ohlc_in_payload_is_a_warning_not_an_error(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 16:15"))
        code, so, se = self.go()
        self.assertEqual(code, 0, se)
        prof = load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))
        self.assertTrue(any("open/high/low/close" in w for w in prof["quality"]["warnings"]))
        row = rows_of(os.path.join(self.out, "history", "SPX_daily.csv"))[0]
        self.assertEqual(row["close"], "")

    def test_insane_ohlc_is_dropped(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 16:15", ohlc=(100, 90, 80, 85)))
        self.go()
        row = rows_of(os.path.join(self.out, "history", "SPX_daily.csv"))[0]
        self.assertEqual(row["close"], "")


class TestIdempotence(Base):
    def test_second_run_changes_nothing(self):
        self.go()
        snap = {}
        for root, _, files in os.walk(self.out):
            for f in files:
                snap[os.path.join(root, f)] = rd(os.path.join(root, f), "rb")
        code, so, se = self.go()
        self.assertEqual(code, 0); self.assertIn("unchanged", so)
        again = {}
        for root, _, files in os.walk(self.out):
            for f in files:
                again[os.path.join(root, f)] = rd(os.path.join(root, f), "rb")
        self.assertEqual(snap, again, "a repeated run must leave every file byte-identical (no useless git commits)")

    def test_newer_snapshot_same_day_overwrites(self):
        self.go()
        write_fixture(self.fx, "_SPX", cboe_payload(7815.0, "2026-10-09 16:25", seed=9))
        code, so, se = self.go()
        self.assertEqual(code, 0, se); self.assertIn("written", so)
        self.assertEqual(load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))["asof"], "2026-10-09T16:25:00")
        self.assertEqual(len(rows_of(os.path.join(self.out, "history", "SPX_daily.csv"))), 1)


class TestTwoDays(Base):
    def test_previous_day_changes(self):
        self.go()
        write_fixture(self.fx, "_SPX", cboe_payload(7840.0, "2026-10-12 16:15", seed=11))
        code, so, se = self.go(NOW2)
        self.assertEqual(code, 0, se)
        t = load(os.path.join(self.out, "2026-10-12", "SPX.json.gz"))["expiry_table"]
        rows = [dict(zip(t["cols"], r)) for r in t["rows"]]
        self.assertIsNotNone(rows[0]["gex_chg_1d"], "total row must carry a 1D change")
        common = [r for r in rows[1:] if r["gex_chg_1d"] is not None]
        self.assertGreater(len(common), 10)
        prev = load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))["expiry_table"]
        pr = {r[0]: dict(zip(prev["cols"], r)) for r in prev["rows"]}
        r0 = common[0]
        self.assertAlmostEqual(r0["gex_chg_1d"], r0["gex"] - pr[r0["expiry"]]["gex"], delta=2)
        rows_h = rows_of(os.path.join(self.out, "history", "SPX_daily.csv"))
        self.assertEqual([r["trade_date"] for r in rows_h], ["2026-10-09", "2026-10-12"])
        idx = jl(os.path.join(self.out, "index.json"))
        self.assertEqual(idx["symbols"]["SPX"]["dates"], ["2026-10-09", "2026-10-12"])


class TestGuards(Base):
    def test_market_closed_weekend(self):
        code, so, se = self.go("2026-10-10T18:45:00")
        self.assertEqual(code, 0); self.assertIn("market closed", so)
        self.assertFalse(os.path.exists(os.path.join(self.out, "2026-10-09")))

    def test_holiday_is_closed(self):
        code, so, se = self.go("2026-11-26T18:45:00")           # Thanksgiving
        self.assertEqual(code, 0); self.assertIn("market closed", so)

    def test_force_on_closed_day_runs(self):
        code, so, se = self.go("2026-10-10T18:45:00", "--force")
        self.assertEqual(code, 0, se); self.assertTrue(os.path.exists(os.path.join(self.out, "2026-10-09", "SPX.json.gz")))

    def test_stale_payload_fails_and_writes_nothing(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-08 16:15"))
        code, so, se = self.go()
        self.assertEqual(code, 1); self.assertIn("stale payload", se)
        self.assertFalse(os.path.exists(os.path.join(self.out, "2026-10-08")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "history", "SPX_daily.csv")))

    def test_intraday_refused_then_allowed(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 11:30"))
        code, so, se = self.go("2026-10-09T11:45:00")
        self.assertEqual(code, 1); self.assertIn("intraday", se)
        code, so, se = self.go("2026-10-09T11:45:00", "--allow-intraday")
        self.assertEqual(code, 0, se)
        self.assertIn("intraday snapshot", load(os.path.join(self.out, "2026-10-09", "SPX.json.gz"))["quality"]["warnings"])

    def test_incomplete_chain_rejected(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 16:15", drop_to=300))
        code, so, se = self.go()
        self.assertEqual(code, 1); self.assertIn("incomplete chain", se)
        self.assertFalse(os.path.exists(os.path.join(self.out, "2026-10-09", "SPX.json.gz")))

    def test_iv_in_percent_rejected(self):
        write_fixture(self.fx, "_SPX", cboe_payload(7811.54, "2026-10-09 16:15", iv_scale=100))
        code, so, se = self.go()
        self.assertEqual(code, 1); self.assertIn("IV field suspect", se)

    def test_missing_fixture_fails_cleanly(self):
        code, so, se = run(["--out", self.out, "--config", CFG, "--symbols", "SPX", "--fixture-dir", os.path.join(self.tmp, "none"), "--now", NOW1])
        self.assertEqual(code, 1); self.assertIn("not found", se)

    def test_one_failing_symbol_does_not_block_the_other(self):
        cfg = jl(CFG); cfg["symbols"]["TST"] = {"cboe": "TST", "min_contracts": 100}
        cp = os.path.join(self.tmp, "cfg.json")
        with open(cp, "w") as fh:
            json.dump(cfg, fh)
        write_fixture(self.fx, "TST", {"timestamp": "2026-10-09 16:15:00", "data": {"current_price": 1, "options": []}})
        code, so, se = run(["--out", self.out, "--config", cp, "--symbols", "TST,SPX", "--fixture-dir", self.fx, "--now", NOW1])
        self.assertEqual(code, 1); self.assertIn("FAIL TST", se)
        self.assertTrue(os.path.exists(os.path.join(self.out, "2026-10-09", "SPX.json.gz")), "SPX must still be written")
        self.assertFalse(os.path.exists(os.path.join(self.out, "2026-10-09", "TST.json.gz")))

    def test_unknown_symbol(self):
        code, so, se = run(["--out", self.out, "--config", CFG, "--symbols", "ZZZ", "--fixture-dir", self.fx, "--now", NOW1])
        self.assertEqual(code, 1); self.assertIn("unknown symbols", se)

    def test_no_temp_files_left(self):
        self.go()
        leftovers = [f for _, _, fs in os.walk(self.out) for f in fs if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestBytesAreDeterministic(Base):
    def test_gzip_has_no_timestamp(self):
        self.go()
        raw = rd(os.path.join(self.out, "2026-10-09", "SPX.json.gz"), "rb")
        self.assertEqual(raw[4:8], b"\x00\x00\x00\x00", "gzip mtime must be 0 so reruns do not change the file")


if __name__ == "__main__":
    unittest.main()
