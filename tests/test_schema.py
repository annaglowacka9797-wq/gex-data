import copy, os, sys, unittest, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from schema import validate_profile
from helpers import cboe_payload
import collect as C
import tempfile


def good_profile():
    cfg = C.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json"))
    raw = cboe_payload(7811.54, "2026-10-09 16:15")
    prof, _ = C.build_profile(raw, "SPX", cfg, None, "2026-10-09T22:45:00Z")
    return prof


class TestSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = good_profile()

    def test_good_profile_is_valid(self):
        self.assertEqual(validate_profile(self.good), [])

    def test_missing_key(self):
        d = copy.deepcopy(self.good); del d["levels"]
        self.assertTrue(any("levels" in e for e in validate_profile(d)))

    def test_nan_is_rejected(self):
        d = copy.deepcopy(self.good); d["by_strike"]["rows"][3][1] = float("nan")
        self.assertTrue(validate_profile(d))

    def test_wrong_columns(self):
        d = copy.deepcopy(self.good); d["by_strike"]["cols"] = ["a"]
        self.assertTrue(validate_profile(d))

    def test_not_an_object(self):
        self.assertEqual(validate_profile([]), ["not an object"])

    def test_bad_iv(self):
        d = copy.deepcopy(self.good); d["em_iv"] = 16.3          # percent instead of fraction
        self.assertTrue(any("em_iv" in e for e in validate_profile(d)))

    def test_json_roundtrip_stays_valid(self):
        self.assertEqual(validate_profile(json.loads(json.dumps(self.good, allow_nan=False))), [])


if __name__ == "__main__":
    unittest.main()
