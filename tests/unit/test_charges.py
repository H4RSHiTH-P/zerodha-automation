"""round_trip() must equal the two inline blocks of performance.py (lines 296-308 and 339-351)."""

import random

from tests.unit.factories import CHARGES as C
from tests.unit.factories import CHARGES_ENV
from zt.backtest.charges import round_trip

E = {k: float(v) for k, v in CHARGES_ENV.items()}


def legacy(side: int, open_amount: float, close_amount: float) -> float:
    is_long = side > 0
    b = min(E["ZERODHA_MAX_BROKERAGE"], open_amount * E["ZERODHA_BROKERAGE"])
    stt = open_amount * E["ZERODHA_STT"] if not is_long else 0
    nse = open_amount * E["ZERODHA_NSE"]
    sebi = open_amount * E["ZERODHA_SEBI"]
    gst = (b + nse + sebi) * E["ZERODHA_GST"]
    stamp = open_amount * E["ZERODHA_STAMP_DUTY"] if is_long else 0
    open_charges = b + stt + nse + sebi + gst + stamp

    b = min(E["ZERODHA_MAX_BROKERAGE"], close_amount * E["ZERODHA_BROKERAGE"])
    stt = close_amount * E["ZERODHA_STT"] if is_long else 0
    nse = close_amount * E["ZERODHA_NSE"]
    sebi = close_amount * E["ZERODHA_SEBI"]
    gst = (b + nse + sebi) * E["ZERODHA_GST"]
    stamp = close_amount * E["ZERODHA_STAMP_DUTY"] if not is_long else 0
    return open_charges + b + stt + nse + sebi + gst + stamp


def test_round_trip_equals_legacy_formulas():
    rng = random.Random(7)
    for _ in range(200):
        side = rng.choice([1, -1])
        a, b = rng.uniform(1000, 1_000_000), rng.uniform(1000, 1_000_000)
        assert abs(round_trip(side, a, b, C) - legacy(side, a, b)) < 1e-9
