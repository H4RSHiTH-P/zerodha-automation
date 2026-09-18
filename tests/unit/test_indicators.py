"""Indicators against independent references (Pine reference implementations transcribed in the test)."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from tests.unit.factories import random_walk
from zt.candles import heikin_ashi
from zt.candles.model import Candle
from zt.core.clock import IST
from zt.strategy.bars import compute_bars
from zt.strategy.indicators import ATR, EMA, RSI, SMA, RelativeVolume, SessionVWAP, Supertrend
from zt.strategy.rules import Params


def sma_ref(closes, n):
    return [None if i < n - 1 else float(np.mean(closes[i - n + 1 : i + 1])) for i in range(len(closes))]


def rsi_ref(closes, n):
    """Wilder RSI seeded with the simple average of the first n changes."""
    out, changes = [None] * len(closes), np.diff(closes)
    ag = al = None
    for i in range(n, len(closes)):
        if i == n:
            ag, al = np.mean(np.maximum(changes[:n], 0)), np.mean(np.maximum(-changes[:n], 0))
        else:
            ch = changes[i - 1]
            ag, al = (ag * (n - 1) + max(ch, 0)) / n, (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema_ref(values, n):
    out, alpha, s = [None] * len(values), 2 / (n + 1), None
    for i, v in enumerate(values):
        if i == n - 1:
            s = float(np.mean(values[:n]))
        elif i >= n:
            s = alpha * v + (1 - alpha) * s
        out[i] = s
    return out


def atr_ref(highs, lows, closes, n):
    trs = [highs[0] - lows[0]] + [
        max(h - lo, abs(h - pc), abs(lo - pc)) for h, lo, pc in zip(highs[1:], lows[1:], closes[:-1], strict=True)
    ]
    out, s = [None] * len(trs), None
    for i, tr in enumerate(trs):
        if i == n - 1:
            s = float(np.mean(trs[:n]))
        elif i >= n:
            s = (tr + (n - 1) * s) / n
        out[i] = s
    return out


def supertrend_ref(highs, lows, closes, n, factor):
    """Direct transcription of Pine's `ta.supertrend` source, index based."""
    atr = atr_ref(highs, lows, closes, n)
    upper, lower, st, direction = [None] * len(closes), [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(len(closes)):
        if atr[i] is None:
            continue
        src = (highs[i] + lows[i]) / 2
        up, lo = src + factor * atr[i], src - factor * atr[i]
        prev_up = upper[i - 1] if i > 0 and upper[i - 1] is not None else 0.0
        prev_lo = lower[i - 1] if i > 0 and lower[i - 1] is not None else 0.0
        lo = lo if (lo > prev_lo or closes[i - 1] < prev_lo) else prev_lo
        up = up if (up < prev_up or closes[i - 1] > prev_up) else prev_up
        if i == 0 or atr[i - 1] is None:
            d = 1
        elif st[i - 1] == prev_up:
            d = -1 if closes[i] > up else 1
        else:
            d = 1 if closes[i] < lo else -1
        upper[i], lower[i], direction[i] = up, lo, d
        st[i] = lo if d == -1 else up
    return st, direction


@pytest.mark.parametrize("seed", [1, 2])
def test_heikin_ashi_formula(seed):
    raw = random_walk(300, seed)
    ha = heikin_ashi.convert_series(raw)
    assert ha[0].open == (raw[0].open + raw[0].close) / 2
    for i, (r, h) in enumerate(zip(raw, ha, strict=True)):
        assert h.close == (r.open + r.high + r.low + r.close) / 4
        assert h.high == max(r.high, h.open, h.close) and h.low == min(r.low, h.open, h.close)
        if i:
            assert h.open == (ha[i - 1].open + ha[i - 1].close) / 2


@pytest.mark.parametrize("seed", [11, 12])
def test_sma_rsi_ema_atr_supertrend_match_references(seed):
    ha = heikin_ashi.convert_series(random_walk(400, seed, vol=0.01))
    sma, rsi, ema, atr, st = SMA(50), RSI(15), EMA(20), ATR(10), Supertrend(10, 3.0)
    got_ema, got_atr, got_st = [], [], []
    for c in ha:
        sma.update(c)
        rsi.update(c)
        got_ema.append(ema.update(c.close))
        got_atr.append(atr.update(c.high, c.low, c.close))
        got_st.append(st.update(c.high, c.low, c.close))
    closes = [c.close for c in ha]
    highs, lows = [c.high for c in ha], [c.low for c in ha]
    ref_st, ref_dir = supertrend_ref(highs, lows, closes, 10, 3.0)
    for i, c in enumerate(ha):
        for got, ref in (
            (c.sma, sma_ref(closes, 50)[i]),
            (c.rsi, rsi_ref(closes, 15)[i]),
            (got_ema[i], ema_ref(closes, 20)[i]),
            (got_atr[i], atr_ref(highs, lows, closes, 10)[i]),
            (got_st[i][0], ref_st[i]),
        ):
            assert (got is None and ref is None) or abs(got - ref) < 1e-6, (i, got, ref)
        assert got_st[i][1] == ref_dir[i]
    assert {d for _, d in got_st if d is not None} == {-1, 1}


def test_session_vwap_resets_each_day():
    d1, d2 = datetime(2026, 9, 17, 9, 15, tzinfo=IST), datetime(2026, 9, 18, 9, 15, tzinfo=IST)
    v = SessionVWAP()
    assert v.update(Candle(10, 12, 8, 10, d1, volume=100)) == 10.0  # hlc3 = 10
    assert abs(v.update(Candle(10, 14, 12, 13, d1 + timedelta(minutes=3), volume=300)) - 12.25) < 1e-12
    assert v.update(Candle(20, 21, 19, 20, d2, volume=50)) == 20.0  # new session
    assert v.update(Candle(20, 21, 19, 20, d2 + timedelta(minutes=3), volume=0)) == 20.0


def test_relative_volume_compares_same_time_of_day_and_tolerates_missing_bars():
    day = datetime(2026, 9, 17, 9, 15, tzinfo=IST)
    rel = RelativeVolume(sessions=10)
    for k in range(3):  # day 1: cumulative 10, 20, 30 at 09:15, 09:18, 09:21
        assert rel.update(Candle(1, 1, 1, 1, day + timedelta(minutes=3 * k), volume=10)) is None
    nxt = day + timedelta(days=1)
    assert rel.update(Candle(1, 1, 1, 1, nxt, volume=20)) == 2.0  # 20 vs 10
    assert rel.update(Candle(1, 1, 1, 1, nxt + timedelta(minutes=3), volume=20)) == 2.0  # 40 vs 20
    # 09:24 did not exist on day 1: compare with the latest bar at or before 09:24 (09:21 -> 30)
    assert rel.update(Candle(1, 1, 1, 1, nxt + timedelta(minutes=9), volume=20)) == 2.0  # 60 vs 30


def test_compute_bars_flags_sessions_and_warms_up():
    raw = random_walk(300, 5)
    bars = compute_bars(raw, Params(activity=True, ema=True, supertrend=True, vwap=True))
    firsts = [b for b in bars if b.is_first]
    lasts = [b for b in bars if b.is_last]
    assert len(firsts) == len(lasts) == len({b.ts.date() for b in bars})
    assert all(b.ts.time().hour == 9 and b.ts.time().minute == 15 for b in firsts)
    assert bars[0].sma is None and bars[60].sma is not None and bars[60].ema is not None and bars[60].st_dir in (-1, 1)
    assert bars[0].rel_vol is None and bars[-1].rel_vol is not None and bars[0].vwap is not None
