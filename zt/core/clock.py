"""Time helpers. Every datetime is tz-aware IST; the database stores epoch seconds."""

from __future__ import annotations

import os
import time as _time
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
IST_OFFSET = timedelta(hours=5, minutes=30)


def ensure_local_tz() -> None:
    """Set the process to IST and assert it. KiteTicker returns naive local datetimes."""
    if datetime.now().astimezone().utcoffset() != IST_OFFSET:
        os.environ["TZ"] = "Asia/Kolkata"
        _time.tzset()
    if datetime.now().astimezone().utcoffset() != IST_OFFSET:
        raise RuntimeError("process timezone is not Asia/Kolkata")


def now() -> datetime:
    return datetime.now(IST)


def today() -> date:
    return now().date()


def to_epoch(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return int(dt.timestamp())


def from_epoch(ts: int | float) -> datetime:
    return datetime.fromtimestamp(ts, IST)


def as_ist(dt: datetime) -> datetime:
    """Kite REST returns +05:30-aware datetimes; the websocket returns naive local ones."""
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def bucket_start(dt: datetime, minutes: int) -> datetime:
    """Start of the N-minute bucket containing dt, anchored at 09:15 of dt's date."""
    dt = as_ist(dt)
    base = at(dt.date(), MARKET_OPEN)
    k = (dt - base) // timedelta(minutes=minutes)
    return base + k * timedelta(minutes=minutes)


def session_end(is_cas: bool) -> time:
    """Continuous trading ends 15:15 for closing-auction-session stocks, 15:30 otherwise."""
    return time(15, 15) if is_cas else time(15, 30)


def square_off(is_cas: bool) -> time:
    """Zerodha's MIS auto square-off."""
    return time(15, 12) if is_cas else time(15, 25)
