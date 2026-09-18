"""Kite historical candles, chunked at 60 days per call (the per-request cap is 60 for 1m, 100 for 3m/5m)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from kiteconnect import KiteConnect

from zt.candles.model import Candle, Interval
from zt.core import clock

CHUNK_DAYS = 60
MIN_GAP_S = 0.5  # historical limit is 3 req/s per key; leave room for other processes


def fetch(kc: KiteConnect, token: int, interval: Interval, start: datetime, end: datetime) -> list[Candle]:
    """Raw candles in [start, end], oldest first, aware IST datetimes, de-duplicated across chunks."""
    out: list[Candle] = []
    seen = set()
    chunk_start = start
    last_call = 0.0
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS) - timedelta(minutes=1), end)
        wait = MIN_GAP_S - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        rows = kc.historical_data(
            token,
            chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
            chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            interval=interval.duration,
        )
        last_call = time.monotonic()
        for row in rows:
            row["date"] = clock.as_ist(row["date"])
            if row["date"] in seen:
                continue
            seen.add(row["date"])
            out.append(Candle.generate(row))
        chunk_start = chunk_end + timedelta(minutes=1)
    out.sort(key=lambda c: c.date_time)
    return out
