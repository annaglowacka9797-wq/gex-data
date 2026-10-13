"""NYSE trading calendar computed by rule (no network, no extra dependency except python-dateutil, which pandas needs anyway).

Holidays covered: New Year's Day, MLK Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth,
Independence Day, Labor Day, Thanksgiving, Christmas, each with NYSE's weekend rules:
  Saturday holiday -> observed on the Friday before (EXCEPT New Year's Day: NYSE stays open on the Dec 31 before),
  Sunday holiday   -> observed on the Monday after.
Early closes (13:00 ET) are not modelled: the collector runs after 16:00 ET anyway.
Special one-off closures (national days of mourning, weather) cannot be known in advance: use `--force` or look at
the 'market closed' / 'stale data' messages of the collector.
"""
from datetime import date, datetime, timedelta

from dateutil.easter import easter


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d, saturday_to_friday=True):
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year):
    """Set of dates the NYSE is closed in `year` (observed dates that fall in `year`)."""
    h = set()
    for d in (_observed(date(year, 1, 1), saturday_to_friday=False),
              _nth_weekday(year, 1, 0, 3),                       # MLK, 3rd Monday of January
              _nth_weekday(year, 2, 0, 3),                       # Washington's Birthday
              easter(year) - timedelta(days=2),                  # Good Friday
              _last_weekday(year, 5, 0),                         # Memorial Day
              _observed(date(year, 6, 19)),                      # Juneteenth
              _observed(date(year, 7, 4)),                       # Independence Day
              _nth_weekday(year, 9, 0, 1),                       # Labor Day
              _nth_weekday(year, 11, 3, 4),                      # Thanksgiving
              _observed(date(year, 12, 25))):                    # Christmas
        if d is not None and d.year == year:
            h.add(d)
    # Independence Day / Christmas observed on the previous Friday can fall in the previous year only for Jan 1 (handled)
    return h


def is_trading_day(d):
    d = d.date() if isinstance(d, datetime) else d
    return d.weekday() < 5 and d not in holidays(d.year)


def prev_trading_day(d):
    d = d.date() if isinstance(d, datetime) else d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def last_finished_session(now_et, close_hour=16, close_minute=30):
    """Date of the latest session whose data should already be final at `now_et` (naive or aware ET datetime).
    Before 16:30 ET on a trading day that is the previous trading day."""
    d = now_et.date()
    after_close = (now_et.hour, now_et.minute) >= (close_hour, close_minute)
    if is_trading_day(d) and after_close:
        return d
    return prev_trading_day(d)
