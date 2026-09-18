"""Heikin Ashi conversion. The formula is the existing `Candle.convert`, unchanged."""

from __future__ import annotations

from zt.candles.model import Candle


def convert(candle: Candle | dict, prev_open=None, prev_close=None) -> Candle:
    """Converts standard candle data into heikin-ashi candle data."""
    if isinstance(candle, Candle):
        standard_open, standard_high = candle.open, candle.high
        standard_low, standard_close = candle.low, candle.close
    else:
        standard_open, standard_high = candle["open"], candle["high"]
        standard_low, standard_close = candle["low"], candle["close"]

    ha_close = (standard_open + standard_high + standard_low + standard_close) / 4
    ha_open = (
        (prev_open + prev_close) / 2
        if prev_open is not None and prev_close is not None
        else (standard_open + standard_close) / 2
    )
    ha_high = max(standard_high, ha_open, ha_close)
    ha_low = min(standard_low, ha_open, ha_close)

    return Candle(
        open=ha_open,
        high=ha_high,
        low=ha_low,
        close=ha_close,
        date_time=candle.date_time if isinstance(candle, Candle) else candle["date"],
        volume=candle.volume if isinstance(candle, Candle) else int(candle.get("volume") or 0),
        tick_count=candle.tick_count if isinstance(candle, Candle) else 0,
    )


def convert_series(raw: list[Candle], prev: Candle | None = None) -> list[Candle]:
    """Chain-converts a raw series, continuing from `prev` (the last HA candle before it) when given."""
    out = []
    prev_open, prev_close = (prev.open, prev.close) if prev else (None, None)
    for candle in raw:
        ha = convert(candle, prev_open, prev_close)
        prev_open, prev_close = ha.open, ha.close
        out.append(ha)
    return out
