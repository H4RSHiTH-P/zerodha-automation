"""Execution rules for the backtest engine and the presets the lab compares."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time

from zt.strategy.rules import Params


@dataclass(frozen=True)
class Rules:
    name: str
    params: Params
    fill: str = "realistic"  # 'realistic': next raw open; 'manual': worse of next open and first-1m close
    slippage_bps: float = 5.0
    risk_fraction: float = 0.10  # of equity per trade, times leverage (Pine: percent_of_equity 10)
    last_entry: time | None = None  # extra entry cutoff; None = Pine (no entry on the session's last bar only)
    self_exit: time | None = None  # close at the next open once a bar closes at/after this; None = last bar close
    sqoff_charge: float = 0.0  # charged when self_exit is set and a position still reaches the last bar


PINE_V2 = Rules("v2", Params(), slippage_bps=0.0)
PINE_V3 = Rules("v3", Params(activity=True), slippage_bps=0.0)
PINE_V4 = Rules(
    "v4",
    Params(activity=True, stop_pct=1.5, rsi_band=5, ema=True, supertrend=True, vwap=True, exit_rule="either"),
    slippage_bps=0.0,
)
REALISTIC = replace(PINE_V2, name="realistic", slippage_bps=5.0)
MANUAL = replace(REALISTIC, name="manual", fill="manual")

PRESETS = {r.name: r for r in (PINE_V2, PINE_V3, PINE_V4, REALISTIC, MANUAL)}
