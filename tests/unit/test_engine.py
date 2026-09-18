"""Engine execution semantics (TradingView broker emulator) on hand-built bars."""

import math
import random
from dataclasses import replace
from datetime import datetime, time, timedelta

from tests.unit.factories import CHARGES
from zt.backtest import engine
from zt.backtest.charges import round_trip
from zt.backtest.presets import MANUAL, PINE_V2, REALISTIC
from zt.candles.model import Candle
from zt.core.clock import IST
from zt.strategy.rules import Bar

T0 = datetime(2026, 9, 17, 10, 0, tzinfo=IST)
INTERVAL_S = 180
EQ, LEV = 100_000.0, 1.0


def bar(i, o, h, lo, c, *, sma=100.0, rsi=50.0, sar=None, last=False, ts=None) -> Bar:
    ts = ts or T0 + timedelta(seconds=INTERVAL_S * i)
    return Bar(
        raw=Candle(o, h, lo, c, ts),
        ha=Candle(o, h, lo, c, ts),
        sma=sma,
        rsi=rsi,
        sar=sar if sar is not None else lo - 5,
        is_last=last,
    )


def long_signal(i, price=110.0, **kw):
    return bar(i, price, price + 1, price - 0.5, price, sma=100, rsi=60, sar=price - 5, **kw)


def short_signal(i, price=90.0, **kw):
    return bar(i, price, price + 0.5, price - 1, price, sma=100, rsi=40, sar=price + 5, **kw)


def neutral(i, o, h, lo, c, **kw):
    return bar(i, o, h, lo, c, sma=100, rsi=50, sar=lo - 5, **kw)


def exit_long_signal(i, o, h, lo, c, **kw):
    return bar(i, o, h, lo, c, sma=100, rsi=50, sar=h + 1, **kw)


def run(bars, rules=PINE_V2, charges=None, m1=None, leverage=LEV):
    return engine.simulate(bars, rules, EQ, leverage, charges, INTERVAL_S, m1)


def test_entry_next_open_then_sar_exit_next_open_with_sizing():
    bars = [
        long_signal(0),
        neutral(1, 111, 112, 110.5, 111.5),
        exit_long_signal(2, 111.5, 112, 111, 111.8),
        neutral(3, 112, 113, 111.5, 112.5),
        neutral(4, 112.5, 113, 112, 112.5, last=True),
    ]
    (t,) = run(bars)
    assert (t.side, t.signal_ts, t.entry_ts, t.entry_price) == (1, bars[0].ts, bars[1].ts, 111)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[3].ts, 112, "EXIT")
    assert t.stop_price == 111 * (1 - 0.025)
    assert t.qty == math.floor(EQ * 0.10 * LEV / 111) == 90 and t.pnl == 90 * 1 and t.charges == 0
    (t5,) = run(bars, leverage=5)
    assert t5.qty == math.floor(EQ * 0.10 * 5 / 111)


def test_slippage_and_charges():
    bars = [
        long_signal(0),
        neutral(1, 111, 112, 110.5, 111.5),
        exit_long_signal(2, 111.5, 112, 111, 111.8),
        neutral(3, 112, 113, 111.5, 112.5, last=True),
    ]
    (t,) = run(bars, REALISTIC, CHARGES)
    assert abs(t.entry_price - 111 * 1.0005) < 1e-9 and abs(t.exit_price - 112 * 0.9995) < 1e-9
    assert abs(t.charges - round_trip(1, t.qty * t.entry_price, t.qty * t.exit_price, CHARGES)) < 1e-9
    assert abs(t.pnl - (t.qty * (t.exit_price - t.entry_price) - t.charges)) < 1e-9


def test_stop_on_the_fill_bar_uses_the_provisional_level():
    provisional = 110 * 0.975  # signal bar HA close
    (t,) = run([long_signal(0), neutral(1, 111, 111.5, 107, 108), neutral(2, 108, 109, 107, 108, last=True)])
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (t.entry_ts, provisional, "STOP")
    (g,) = run([long_signal(0), neutral(1, 106, 107, 105, 106), neutral(2, 106, 107, 105, 106, last=True)])
    assert g.exit_price == 106 and g.exit_reason == "STOP"  # gapped through: filled at the open


def test_stop_is_re_anchored_to_the_fill_from_the_next_bar():
    anchored = 111 * 0.975  # 108.225 > provisional 107.25
    bars = [
        long_signal(0),
        neutral(1, 111, 112, 108.0, 111.5),
        neutral(2, 111.5, 112, 108.0, 111),
        neutral(3, 111, 112, 110, 111, last=True),
    ]
    (t,) = run(bars)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[2].ts, anchored, "STOP")


def test_queued_close_executes_at_the_open_before_the_stop():
    bars = [
        long_signal(0),
        neutral(1, 111, 112, 110.5, 111.5),
        exit_long_signal(2, 111.5, 112, 111, 111.8),
        neutral(3, 112, 113, 100, 101),
        neutral(4, 101, 102, 100, 101, last=True),
    ]
    (t,) = run(bars)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[3].ts, 112, "EXIT")


def test_last_bar_no_entry_and_flat_at_its_close():
    bars = [neutral(0, 110, 111, 109, 110), long_signal(1), neutral(2, 111, 112, 110.5, 111.7, last=True)]
    (t,) = run(bars)
    assert (t.entry_ts, t.exit_ts, t.exit_price, t.exit_reason) == (bars[2].ts, bars[2].ts, 111.7, "EOD")
    assert run([neutral(0, 110, 111, 109, 110), long_signal(1, last=True), long_signal(2)]) == []


def test_no_pyramiding_and_short_gap_stop():
    bars = [
        long_signal(0),
        long_signal(1, 111),
        long_signal(2, 112),
        exit_long_signal(3, 112, 113, 111, 112),
        neutral(4, 113, 114, 112, 113, last=True),
    ]
    trades = run(bars)
    assert len(trades) == 1 and trades[0].entry_ts == bars[1].ts
    bars = [short_signal(0), bar(1, 90.5, 91, 89.5, 90.5, sar=96), bar(2, 94, 95, 93.5, 94, sar=96, last=True)]
    (s,) = run(bars)
    assert (s.side, s.entry_price, s.exit_price, s.exit_reason) == (
        -1,
        90.5,
        94,
        "STOP",
    )  # gap: max(stop 92.76, open 94)
    assert s.pnl == s.qty * (90.5 - 94)


def test_manual_fill_is_the_worse_of_open_and_first_minute_close():
    bars = [
        long_signal(0),
        neutral(1, 111, 112, 110.5, 111.5),
        exit_long_signal(2, 111.5, 112, 111, 111.8),
        neutral(3, 112, 113, 111.5, 112.5, last=True),
    ]
    m1 = {bars[1].ts: 111.4, bars[3].ts: 111.9}
    (t,) = run(bars, MANUAL, m1=m1)
    assert abs(t.entry_price - 111.4 * 1.0005) < 1e-9 and abs(t.exit_price - 111.9 * 0.9995) < 1e-9
    (u,) = run(bars, MANUAL, m1={bars[1].ts: 110.0})  # first-minute close better than open: open wins
    assert abs(u.entry_price - 111 * 1.0005) < 1e-9 and abs(u.exit_price - 112 * 0.9995) < 1e-9


def at(h, m):
    return T0.replace(hour=h, minute=m)


def test_last_entry_cutoff_and_self_exit_deadline():
    rules = replace(PINE_V2, last_entry=time(14, 15), self_exit=time(15, 10), sqoff_charge=59.0)
    bars = [
        long_signal(0, ts=at(14, 12)),
        neutral(1, 111, 112, 110.5, 111.5, ts=at(14, 15)),
        neutral(2, 111.5, 112, 111, 111.5, ts=at(15, 6)),
        neutral(3, 111.5, 112, 111, 111.5, ts=at(15, 9)),
        neutral(4, 112, 113, 111.5, 112.5, ts=at(15, 12)),
        neutral(5, 112.5, 113, 112, 112.5, ts=at(15, 27), last=True),
    ]
    (t,) = run(bars, rules)
    assert (t.entry_ts, t.exit_ts, t.exit_price, t.exit_reason) == (bars[1].ts, bars[4].ts, 112, "DEADLINE")
    assert run([long_signal(0, ts=at(14, 15)), neutral(1, 111, 112, 110, 111, ts=at(14, 18), last=True)], rules) == []
    no_cutoff = replace(rules, last_entry=None)
    (e,) = run([long_signal(0, ts=at(15, 6)), neutral(1, 111, 112, 110, 111, ts=at(15, 9), last=True)], no_cutoff)
    assert (e.exit_ts, e.exit_reason, e.charges) == (at(15, 9), "EOD", 59.0)


def test_invariants_on_random_bars():
    rng = random.Random(3)
    bars, price, ts = [], 100.0, T0
    for i in range(3000):
        o = price
        c = o * (1 + rng.gauss(0, 0.01))
        h, lo = max(o, c) * (1 + abs(rng.gauss(0, 0.004))), min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
        bars.append(
            bar(
                i,
                o,
                h,
                lo,
                c,
                sma=rng.choice([o * 0.98, o * 1.02]),
                rsi=rng.uniform(30, 70),
                sar=rng.choice([lo * 0.99, h * 1.01]),
                ts=ts,
                last=(i % 125 == 124),
            )
        )
        price, ts = c, ts + timedelta(seconds=INTERVAL_S) if i % 125 != 124 else T0 + timedelta(days=i // 125 + 1)
    trades = run(bars, REALISTIC, CHARGES)
    assert len(trades) > 50
    by_ts = {b.ts: b for b in bars}
    for a, b in zip(trades, trades[1:], strict=False):
        assert b.entry_ts > a.exit_ts
    for t in trades:
        assert t.entry_ts > t.signal_ts and t.exit_ts >= t.entry_ts and t.entry_ts.date() == t.exit_ts.date()
        assert t.qty > 0 and t.charges > 0
        if t.exit_reason == "EOD":
            assert by_ts[t.exit_ts].is_last
        if t.exit_reason == "STOP" and t.exit_ts != t.entry_ts:
            assert (t.exit_price <= t.stop_price) if t.side > 0 else (t.exit_price >= t.stop_price)
