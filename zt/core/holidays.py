"""NSE trading holidays, shipped in code (NSE's holiday JSON forbids automated collection).

2026: the dates after 16 Sep 2026 (02 Oct, 20 Oct, 10 Nov, 24 Nov, 25 Dec) were verified against NSE on
16 Sep 2026; the earlier 2026 dates are from the published calendar and only affect past-date arithmetic.
Add the next year's list from NSE's December circular before 1 January.
"""

from __future__ import annotations

from datetime import date, timedelta

HOLIDAYS: dict[int, frozenset[date]] = {
    2026: frozenset(
        {
            date(2026, 1, 26),  # Republic Day
            date(2026, 3, 3),  # Holi
            date(2026, 3, 26),  # Ram Navami
            date(2026, 3, 31),  # Mahavir Jayanti
            date(2026, 4, 3),  # Good Friday
            date(2026, 4, 14),  # Ambedkar Jayanti
            date(2026, 5, 1),  # Maharashtra Day
            date(2026, 5, 28),  # Bakri Id
            date(2026, 6, 26),  # Muharram
            date(2026, 9, 14),  # Ganesh Chaturthi
            date(2026, 10, 2),  # Gandhi Jayanti
            date(2026, 10, 20),  # Dussehra
            date(2026, 11, 10),  # Diwali Balipratipada
            date(2026, 11, 24),  # Guru Nanak Jayanti
            date(2026, 12, 25),  # Christmas
        }
    ),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS.get(d.year, frozenset())


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def has_calendar(year: int) -> bool:
    return year in HOLIDAYS
