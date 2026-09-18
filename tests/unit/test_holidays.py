from datetime import date

from zt.core import holidays


def test_weekends_and_holidays_are_not_trading_days():
    assert not holidays.is_trading_day(date(2026, 9, 19))  # Saturday
    assert not holidays.is_trading_day(date(2026, 10, 2))  # Gandhi Jayanti
    assert holidays.is_trading_day(date(2026, 9, 18))


def test_prev_trading_day_skips_weekend_and_holiday():
    assert holidays.prev_trading_day(date(2026, 9, 21)) == date(2026, 9, 18)  # Monday -> Friday
    assert holidays.prev_trading_day(date(2026, 10, 5)) == date(2026, 10, 1)  # Mon -> Thu (Fri 2 Oct holiday)
    assert holidays.next_trading_day(date(2026, 10, 1)) == date(2026, 10, 5)


def test_calendar_present_for_current_year():
    assert holidays.has_calendar(2026)
