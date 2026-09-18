"""Strategy parameters and the entry/exit conditions, transcribed from resources/pine/scalping_strategy_v2..v4.pine.

Every v3/v4 filter is a switch (default off) so its contribution can be measured; with all switches off the
conditions are exactly v2. Indicators that are still warming up compare as False, like `na` in Pine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zt.candles.model import Candle


class Position(Enum):
    NONE = 0
    LONG = 1
    SHORT = -1


@dataclass(frozen=True)
class Params:
    sma_length: int = 50
    rsi_length: int = 15
    sar: tuple[float, float, float] = (0.02, 0.02, 0.2)
    variation: float = 0.00075  # minimum SAR distance as a fraction of the SMA ("variationPercent")
    stop_pct: float = 2.5
    rsi_band: float = 0.0  # v4: RSI must be beyond 50 +/- band
    activity: bool = False  # v3: relative session volume filter
    activity_sessions: int = 10
    activity_multiple: float = 1.5
    ema: bool = False  # v4: HA close beyond EMA and EMA beyond SMA
    ema_length: int = 20
    supertrend: bool = False  # v4: Supertrend direction on the HA series
    st_length: int = 10
    st_multiplier: float = 3.0
    vwap: bool = False  # v4: HA close beyond session VWAP of raw hlc3
    exit_rule: str = "sar"  # 'sar' | 'supertrend' | 'either'


@dataclass
class Bar:
    raw: Candle
    ha: Candle
    is_first: bool = False  # first candle of its session
    is_last: bool = False  # last candle of its session
    sma: float | None = None
    rsi: float | None = None
    sar: float | None = None
    ema: float | None = None
    st_dir: int | None = None  # -1 up-trend, +1 down-trend (Pine convention)
    vwap: float | None = None
    rel_vol: float | None = None

    @property
    def ts(self):
        return self.raw.date_time


def _ready(b: Bar, p: Params) -> bool:
    needed = [b.sma, b.rsi, b.sar]
    if p.ema:
        needed.append(b.ema)
    if p.supertrend or p.exit_rule != "sar":
        needed.append(b.st_dir)
    if p.vwap:
        needed.append(b.vwap)
    return all(v is not None for v in needed)


def is_active(b: Bar, p: Params) -> bool:
    return not p.activity or (b.rel_vol is not None and b.rel_vol > p.activity_multiple)


def short_entry(b: Bar, p: Params) -> bool:
    return (
        _ready(b, p)
        and b.ha.high < b.sar
        and b.ha.high < b.sma
        and (b.sar - b.ha.high) / b.sma > p.variation
        and b.rsi < 50 - p.rsi_band
        and (not p.ema or (b.ha.close < b.ema and b.ema < b.sma))
        and (not p.supertrend or b.st_dir > 0)
        and (not p.vwap or b.ha.close < b.vwap)
    )


def long_entry(b: Bar, p: Params) -> bool:
    return (
        _ready(b, p)
        and b.ha.low > b.sar
        and b.ha.low > b.sma
        and (b.ha.low - b.sar) / b.sma > p.variation
        and b.rsi > 50 + p.rsi_band
        and (not p.ema or (b.ha.close > b.ema and b.ema > b.sma))
        and (not p.supertrend or b.st_dir < 0)
        and (not p.vwap or b.ha.close > b.vwap)
    )


def exit_short(b: Bar, p: Params) -> bool:
    sar_exit = b.sar is not None and b.sar < b.ha.low
    st_exit = b.st_dir is not None and b.st_dir < 0
    return {"sar": sar_exit, "supertrend": st_exit, "either": sar_exit or st_exit}[p.exit_rule]


def exit_long(b: Bar, p: Params) -> bool:
    sar_exit = b.sar is not None and b.sar > b.ha.high
    st_exit = b.st_dir is not None and b.st_dir > 0
    return {"sar": sar_exit, "supertrend": st_exit, "either": sar_exit or st_exit}[p.exit_rule]


def stop_level(side: Position, price: float, p: Params) -> float:
    return price * (1 - p.stop_pct / 100) if side == Position.LONG else price * (1 + p.stop_pct / 100)
