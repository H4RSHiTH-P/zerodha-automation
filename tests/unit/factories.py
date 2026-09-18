"""Shared test data: environment dictionaries, config objects and a random-walk candle generator."""

import random
from datetime import datetime, timedelta

from zt.candles.model import Candle
from zt.config import Charges, Performance
from zt.core.clock import IST

CHARGES_ENV = {
    "ZERODHA_MAX_BROKERAGE": "20",
    "ZERODHA_BROKERAGE": "0.0003",
    "ZERODHA_STT": "0.00025",
    "ZERODHA_NSE": "0.0000307",
    "ZERODHA_BSE": "0.0000375",
    "ZERODHA_SEBI": "0.000001",
    "ZERODHA_GST": "0.18",
    "ZERODHA_STAMP_DUTY": "0.00003",
}
PERFORMANCE_ENV = {
    "PERFORMANCE_EQUITY_BALANCE": "100000",
    "PERFORMANCE_HISTORIC_DURATION": "90",
    "PERFORMANCE_STABILITY_SHARPE_RATIO": "0.5",
    "PERFORMANCE_STABILITY_PROFIT_FACTOR": "1.1",
    "PERFORMANCE_MIN_POSITIONS": "40",
    "PERFORMANCE_MIN_PROFIT_FACTOR": "1.3",
    "PERFORMANCE_MIN_SHARPE_RATIO": "1.0",
    "PERFORMANCE_MAX_DRAWDOWN_PERCENTAGE": "0.10",
    "PERFORMANCE_MIN_RECOVERY_FACTOR": "2",
    "PERFORMANCE_MIN_EXPECTANCY_RATE": "0",
    "PERFORMANCE_MIN_SCORE": "0.5",
}
FULL_ENV = {"KITE_API_KEY": "key", "KITE_API_SECRET_TOKEN": "secret", **CHARGES_ENV, **PERFORMANCE_ENV}

CHARGES = Charges(**{k.removeprefix("ZERODHA_").lower(): float(v) for k, v in CHARGES_ENV.items()})
PERFORMANCE = Performance(
    **{
        k.removeprefix("PERFORMANCE_").lower(): (int(v) if k.endswith(("DURATION", "POSITIONS")) else float(v))
        for k, v in PERFORMANCE_ENV.items()
    }
)


def random_walk(n: int, seed: int, minutes: int = 3, start_price: float = 500.0, vol: float = 0.003) -> list[Candle]:
    """Raw candles on a 3m/5m grid across trading days (09:15 .. session end), random walk with wicks."""
    rng = random.Random(seed)
    out, price = [], start_price
    day = datetime(2026, 6, 1, tzinfo=IST)
    t = day.replace(hour=9, minute=15)
    for _ in range(n):
        o = price
        c = max(1.0, o * (1 + rng.gauss(0, vol)))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
        low = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
        out.append(Candle(open=o, high=h, low=low, close=c, date_time=t, volume=rng.randint(1, 5000)))
        price = c
        t += timedelta(minutes=minutes)
        if t.hour * 60 + t.minute >= 15 * 60 + 30:
            day += timedelta(days=1)
            t = day.replace(hour=9, minute=15)
    return out
