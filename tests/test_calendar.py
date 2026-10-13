import os, sys, unittest
from datetime import date, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from us_calendar import holidays, is_trading_day, prev_trading_day, last_finished_session


class TestCalendar(unittest.TestCase):
    def test_2026_holidays(self):
        exp = ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"]
        self.assertEqual(sorted(d.strftime("%m-%d") for d in holidays(2026)), exp)

    def test_2027_holidays_weekend_rules(self):
        exp = ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"]
        self.assertEqual(sorted(d.strftime("%m-%d") for d in holidays(2027)), exp)

    def test_new_year_saturday_does_not_close_december_31(self):
        self.assertTrue(is_trading_day(date(2027, 12, 31)))          # Jan 1 2028 is a Saturday
        self.assertNotIn(date(2027, 12, 31), holidays(2027))

    def test_weekend_and_prev(self):
        self.assertFalse(is_trading_day(date(2026, 10, 10)))
        self.assertEqual(prev_trading_day(date(2026, 10, 12)), date(2026, 10, 9))
        self.assertEqual(prev_trading_day(date(2026, 7, 6)), date(2026, 7, 2))      # Jul 3 observed holiday

    def test_last_finished_session(self):
        self.assertEqual(last_finished_session(datetime(2026, 10, 9, 18, 45)), date(2026, 10, 9))
        self.assertEqual(last_finished_session(datetime(2026, 10, 9, 12, 0)), date(2026, 10, 8))
        self.assertEqual(last_finished_session(datetime(2026, 10, 10, 18, 45)), date(2026, 10, 9))


if __name__ == "__main__":
    unittest.main()
