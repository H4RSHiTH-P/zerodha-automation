"""Streaming indicators. SMA, RSI and Parabolic SAR are the original classes, unchanged; EMA, ATR, Supertrend,
session VWAP and relative volume follow the Pine built-ins used by resources/pine/scalping_strategy_v3/v4.

SAR follows TradingView `pine_sar` semantics (reversal tested on the unclamped SAR), not Wilder/TA-Lib.
"""

from bisect import bisect_right
from collections import deque

from zt.candles.model import Candle


class SMA:
    def __init__(self, length: int = 50):
        self.length = length
        self.window = deque(maxlen=length)
        self.sum = 0.0

    def update(self, candle: Candle) -> None:
        if len(self.window) == self.length:
            self.sum -= self.window[0]

        self.window.append(candle.close)
        self.sum += candle.close

        if len(self.window) == self.length:
            candle.sma = self.sum / self.length


class RSI:
    def __init__(self, length: int = 15):
        self.length = length
        self.prev_close = None
        self.avg_gain = None
        self.avg_loss = None
        self.count = 0

    def update(self, candle: Candle) -> None:
        if self.prev_close is None:
            self.prev_close = candle.close
        else:
            change = candle.close - self.prev_close
            gain = max(change, 0.0)
            loss = max(-change, 0.0)

            self.prev_close = candle.close
            self.count += 1

            if self.count < self.length:
                if self.avg_gain is None:
                    self.avg_gain = 0.0
                    self.avg_loss = 0.0
                self.avg_gain += gain
                self.avg_loss += loss
            else:
                if self.count == self.length:
                    self.avg_gain = (self.avg_gain + gain) / self.length
                    self.avg_loss = (self.avg_loss + loss) / self.length
                else:
                    self.avg_gain = ((self.avg_gain * (self.length - 1)) + gain) / self.length
                    self.avg_loss = ((self.avg_loss * (self.length - 1)) + loss) / self.length

                if self.avg_loss == 0:
                    candle.rsi = 100.0
                else:
                    rs = self.avg_gain / self.avg_loss
                    rsi = 100 - (100 / (1 + rs))
                    candle.rsi = rsi


class SAR:
    def __init__(self, start: float = 0.02, increment: float = 0.02, max_acceleration: float = 0.2):
        self.start = start
        self.increment = increment
        self.max_acceleration = max_acceleration

        self.sar = None
        self.extreme = None
        self.acceleration = None
        self.is_below = None

        self.prev_close = None
        self.prev_highs = []
        self.prev_lows = []

        self.initialized = False

    def update(self, candle: Candle) -> None:
        if self.prev_close is None:
            self.prev_close = candle.close
            self.prev_highs.append(candle.high)
            self.prev_lows.append(candle.low)
            return

        if not self.initialized:
            if candle.close > self.prev_close:
                self.is_below = True
                self.extreme = candle.high
                self.sar = self.prev_lows[-1]
            else:
                self.is_below = False
                self.extreme = candle.low
                self.sar = self.prev_highs[-1]

            self.acceleration = self.start
            self.initialized = True

            self.prev_close = candle.close
            self.prev_highs.append(candle.high)
            self.prev_lows.append(candle.low)
            candle.sar = self.sar
            return

        is_first_trend_bar = False
        self.sar = self.sar + self.acceleration * (self.extreme - self.sar)

        if self.is_below:
            if self.sar > candle.low:
                is_first_trend_bar = True
                self.is_below = False
                self.sar = max(candle.high, self.extreme)
                self.extreme = candle.low
                self.acceleration = self.start
        else:
            if self.sar < candle.high:
                is_first_trend_bar = True
                self.is_below = True
                self.sar = min(candle.low, self.extreme)
                self.extreme = candle.high
                self.acceleration = self.start

        if not is_first_trend_bar:
            if self.is_below:
                if candle.high > self.extreme:
                    self.extreme = candle.high
                    self.acceleration = min(self.acceleration + self.increment, self.max_acceleration)
            else:
                if candle.low < self.extreme:
                    self.extreme = candle.low
                    self.acceleration = min(self.acceleration + self.increment, self.max_acceleration)

        if self.is_below:
            self.sar = min(self.sar, self.prev_lows[-1])
            if len(self.prev_lows) > 1:
                self.sar = min(self.sar, self.prev_lows[-2])
        else:
            self.sar = max(self.sar, self.prev_highs[-1])
            if len(self.prev_highs) > 1:
                self.sar = max(self.sar, self.prev_highs[-2])

        self.prev_close = candle.close
        self.prev_highs.append(candle.high)
        self.prev_lows.append(candle.low)
        candle.sar = self.sar

        if len(self.prev_highs) > 2:
            self.prev_highs.pop(0)
        if len(self.prev_lows) > 2:
            self.prev_lows.pop(0)


class EMA:
    """Pine `ta.ema`: seeded with the SMA of the first `length` values."""

    def __init__(self, length: int = 20):
        self.length, self.alpha = length, 2 / (length + 1)
        self.seed: list[float] = []
        self.value = None

    def update(self, value: float) -> float | None:
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
        else:
            self.value = self.alpha * value + (1 - self.alpha) * self.value
        return self.value


class ATR:
    """Pine `ta.atr`: RMA of the true range, seeded with the SMA of the first `length` ranges."""

    def __init__(self, length: int = 10):
        self.length = length
        self.prev_close = None
        self.seed: list[float] = []
        self.value = None

    def update(self, high: float, low: float, close: float) -> float | None:
        tr = (
            high - low
            if self.prev_close is None
            else max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        )
        self.prev_close = close
        if self.value is None:
            self.seed.append(tr)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
        else:
            self.value = (tr + (self.length - 1) * self.value) / self.length
        return self.value


class Supertrend:
    """Pine `ta.supertrend(factor, atrPeriod)`; direction -1 = up-trend, +1 = down-trend."""

    def __init__(self, length: int = 10, multiplier: float = 3.0):
        self.atr = ATR(length)
        self.multiplier = multiplier
        self.prev_close = None
        self.prev_upper = self.prev_lower = self.prev_value = None
        self.prev_atr = None
        self.value = self.direction = None

    def update(self, high: float, low: float, close: float) -> tuple[float | None, int | None]:
        atr = self.atr.update(high, low, close)
        if atr is None:
            self.prev_close = close
            return None, None
        src = (high + low) / 2
        upper, lower = src + self.multiplier * atr, src - self.multiplier * atr
        if self.prev_lower is not None:
            lower = lower if (lower > self.prev_lower or self.prev_close < self.prev_lower) else self.prev_lower
            upper = upper if (upper < self.prev_upper or self.prev_close > self.prev_upper) else self.prev_upper
        if self.prev_atr is None:
            direction = 1
        elif self.prev_value == self.prev_upper:
            direction = -1 if close > upper else 1
        else:
            direction = 1 if close < lower else -1
        value = lower if direction == -1 else upper
        self.prev_close, self.prev_upper, self.prev_lower = close, upper, lower
        self.prev_value, self.prev_atr = value, atr
        self.value, self.direction = value, direction
        return value, direction


class SessionVWAP:
    """Pine `ta.vwap(hlc3)`: volume-weighted average anchored at the session open."""

    def __init__(self):
        self.date = None
        self.pv = self.v = 0.0

    def update(self, candle: Candle) -> float | None:
        if candle.date_time.date() != self.date:
            self.date, self.pv, self.v = candle.date_time.date(), 0.0, 0.0
        self.pv += (candle.high + candle.low + candle.close) / 3 * candle.volume
        self.v += candle.volume
        return self.pv / self.v if self.v > 0 else None


class RelativeVolume:
    """v3 activity filter: volume traded so far today against the same time of day in the previous sessions.

    The Pine version indexes previous sessions by bar offset, which drifts when a candle is missing; this one
    compares by time of day.
    """

    def __init__(self, sessions: int = 10):
        self.history: deque[tuple[list, list]] = deque(maxlen=sessions)
        self.date = None
        self.tods: list = []
        self.cums: list[float] = []
        self.cum = 0.0

    def update(self, candle: Candle) -> float | None:
        if candle.date_time.date() != self.date:
            if self.date is not None:
                self.history.append((self.tods, self.cums))
            self.date, self.tods, self.cums, self.cum = candle.date_time.date(), [], [], 0.0
        self.cum += candle.volume
        tod = candle.date_time.time()
        self.tods.append(tod)
        self.cums.append(self.cum)
        total, used = 0.0, 0
        for tods, cums in self.history:
            i = bisect_right(tods, tod) - 1
            if i >= 0 and cums[i] > 0:
                total += cums[i]
                used += 1
        return self.cum / (total / used) if used else None
