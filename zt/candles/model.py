"""Candle model. `Candle.generate/create/update/aggregate` are the existing methods plus volume and tick counts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Interval(Enum):
    ONE_MINUTE = ("minute", "1m", 60)
    THREE_MINUTE = ("3minute", "3m", 180)
    FIVE_MINUTE = ("5minute", "5m", 300)

    def __init__(self, duration: str, abbreviation: str, seconds: int):
        self._duration = duration
        self._abbreviation = abbreviation
        self._seconds = seconds

    @property
    def duration(self) -> str:
        """Kite historical API name."""
        return self._duration

    @property
    def abbreviation(self) -> str:
        """Database / config name."""
        return self._abbreviation

    @property
    def seconds(self) -> int:
        return self._seconds

    @classmethod
    def from_abbreviation(cls, abbreviation: str) -> Interval:
        for member in cls:
            if member.abbreviation == abbreviation:
                return member
        raise ValueError(abbreviation)


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    date_time: datetime
    volume: int = 0
    tick_count: int = 0

    sma: float = None
    rsi: float = None
    sar: float = None

    @staticmethod
    def generate(candle: dict) -> Candle:
        """Candle from a Kite historical row."""
        return Candle(
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            date_time=candle["date"],
            volume=int(candle.get("volume") or 0),
        )

    @staticmethod
    def create(tick: dict, date_time: datetime) -> Candle:
        """Creates a new candle based on the first tick obtained from the Web Socket."""
        return Candle(
            open=tick["last_price"],
            high=tick["last_price"],
            low=tick["last_price"],
            close=tick["last_price"],
            date_time=date_time,
            tick_count=1,
        )

    def update(self, tick: dict, volume_delta: int = 0) -> None:
        """Updates candle based on the consecutive ticks obtained from the Web Socket."""
        self.high = max(self.high, tick["last_price"])
        self.low = min(self.low, tick["last_price"])
        self.close = tick["last_price"]
        self.volume += volume_delta
        self.tick_count += 1

    @staticmethod
    def aggregate(candles: list[Candle], date_time: datetime) -> Candle:
        """Aggregates set of candles data into single candle data."""
        return Candle(
            open=candles[0].open,
            high=max(candle.high for candle in candles),
            low=min(candle.low for candle in candles),
            close=candles[-1].close,
            date_time=date_time,
            volume=sum(candle.volume for candle in candles),
            tick_count=sum(candle.tick_count for candle in candles),
        )
