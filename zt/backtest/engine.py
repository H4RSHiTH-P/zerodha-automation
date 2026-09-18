"""Pure backtest engine with TradingView's execution semantics (no I/O, clock or environment).

Per bar, in order: (1) fill the order queued at the previous close at this bar's open; (2) the resting stop,
intrabar, filled at the stop level or at the open when the bar gaps through it; (3) on the session's last bar,
close at its close (`strategy.close_all(immediately=true)`); (4) evaluate the closed bar: queue an exit for the
next open, or, when flat and not on the last bar, queue an entry. The stop on the fill bar is the provisional
level from the signal bar's HA close; from the next bar it is re-anchored to the fill price, as in the Pine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from zt.backtest.charges import round_trip
from zt.backtest.presets import Rules
from zt.candles.model import Candle
from zt.config import Charges
from zt.strategy import rules as R
from zt.strategy.bars import compute_bars
from zt.strategy.rules import Bar, Position


@dataclass(frozen=True)
class Trade:
    n: int
    side: int  # +1 long, -1 short
    qty: int
    signal_ts: datetime
    entry_ts: datetime
    entry_price: float
    stop_price: float  # re-anchored stop (from the fill)
    exit_ts: datetime
    exit_price: float
    exit_reason: str  # 'EXIT' (trend flip) | 'STOP' | 'DEADLINE' | 'EOD'
    charges: float
    pnl: float


@dataclass(frozen=True)
class BacktestResult:
    rules: Rules
    trades: list[Trade]
    bars: int
    trading_dates: list
    window_from: datetime | None
    window_to: datetime | None


@dataclass
class _Open:
    side: Position
    qty: int
    signal_bar: Bar
    entry_bar_index: int
    entry_ts: datetime
    entry_price: float
    provisional_stop: float
    stop: float


def _slip(price: float, is_buy: bool, bps: float) -> float:
    return price * (1 + bps / 10_000) if is_buy else price * (1 - bps / 10_000)


def _fill(rules: Rules, bar: Bar, is_buy: bool, m1: dict | None) -> float:
    price = bar.raw.open
    if rules.fill == "manual" and m1 is not None:
        first_minute_close = m1.get(bar.raw.date_time)
        if first_minute_close is not None:
            price = max(price, first_minute_close) if is_buy else min(price, first_minute_close)
    return _slip(price, is_buy, rules.slippage_bps)


def _close_time(bar: Bar, interval_s: int):
    return (bar.raw.date_time + timedelta(seconds=interval_s)).time()


def simulate(
    bars: list[Bar],
    rules: Rules,
    equity: float,
    leverage: float,
    charges: Charges | None,
    interval_s: int,
    m1: dict | None = None,
) -> list[Trade]:
    p = rules.params
    trades: list[Trade] = []
    open_: _Open | None = None
    pending: tuple | None = None  # ('entry', Position, signal_bar) | ('close', reason)
    cash = equity

    def close(exit_price: float, ts: datetime, reason: str) -> None:
        nonlocal open_, cash
        o = open_
        side = o.side.value
        cost = round_trip(side, o.qty * o.entry_price, o.qty * exit_price, charges) if charges else 0.0
        if reason == "EOD" and rules.self_exit is not None:
            cost += rules.sqoff_charge
        pnl = side * o.qty * (exit_price - o.entry_price) - cost
        trades.append(
            Trade(
                len(trades) + 1,
                side,
                o.qty,
                o.signal_bar.ts,
                o.entry_ts,
                o.entry_price,
                o.stop,
                ts,
                exit_price,
                reason,
                cost,
                pnl,
            )
        )
        cash += pnl
        open_ = None

    for k, b in enumerate(bars):
        # 1. queued market order fills at this bar's open
        if pending is not None:
            kind = pending[0]
            if kind == "entry" and open_ is None:
                _, side, signal_bar = pending
                price = _fill(rules, b, side == Position.LONG, m1)
                qty = math.floor(cash * rules.risk_fraction * leverage / price)
                if qty > 0:
                    open_ = _Open(
                        side,
                        qty,
                        signal_bar,
                        k,
                        b.ts,
                        price,
                        R.stop_level(side, signal_bar.ha.close, p),
                        R.stop_level(side, price, p),
                    )
            elif kind == "close" and open_ is not None:
                close(_fill(rules, b, open_.side == Position.SHORT, m1), b.ts, pending[1])
            pending = None

        # 2. resting stop, intrabar
        if open_ is not None:
            stop = open_.provisional_stop if open_.entry_bar_index == k else open_.stop
            if open_.side == Position.LONG and b.raw.low <= stop:
                close(min(stop, b.raw.open), b.ts, "STOP")
            elif open_.side == Position.SHORT and b.raw.high >= stop:
                close(max(stop, b.raw.open), b.ts, "STOP")

        # 3. last bar of the session: flat at its close
        if open_ is not None and b.is_last:
            close(b.raw.close, b.ts, "EOD")

        # 4. decisions on the closed bar, executed at the next open
        if open_ is not None:
            if rules.self_exit is not None and _close_time(b, interval_s) >= rules.self_exit:
                pending = ("close", "DEADLINE")
            elif R.exit_short(b, p) if open_.side == Position.SHORT else R.exit_long(b, p):
                pending = ("close", "EXIT")
        elif (
            not b.is_last
            and R.is_active(b, p)
            and (rules.last_entry is None or _close_time(b, interval_s) <= rules.last_entry)
        ):
            if R.short_entry(b, p):
                pending = ("entry", Position.SHORT, b)
            elif R.long_entry(b, p):
                pending = ("entry", Position.LONG, b)
    return trades


def run(
    raw: list[Candle],
    rules: Rules,
    equity: float,
    leverage: float,
    charges: Charges | None,
    interval_s: int,
    m1: dict | None = None,
) -> BacktestResult:
    bars = compute_bars(raw, rules.params)
    trades = simulate(bars, rules, equity, leverage, charges, interval_s, m1)
    dates = sorted({b.ts.date() for b in bars})
    return BacktestResult(
        rules, trades, len(bars), dates, raw[0].date_time if raw else None, raw[-1].date_time if raw else None
    )
