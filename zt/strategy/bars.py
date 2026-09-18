"""Raw candles -> Bars: Heikin Ashi chain, indicators on the HA series, session first/last flags."""

from __future__ import annotations

from zt.candles import heikin_ashi
from zt.candles.model import Candle
from zt.strategy.indicators import EMA, RSI, SAR, SMA, RelativeVolume, SessionVWAP, Supertrend
from zt.strategy.rules import Bar, Params


def compute_bars(raw: list[Candle], p: Params) -> list[Bar]:
    ha = heikin_ashi.convert_series(raw)
    sma, rsi, sar = SMA(p.sma_length), RSI(p.rsi_length), SAR(*p.sar)
    ema, st = EMA(p.ema_length), Supertrend(p.st_length, p.st_multiplier)
    vwap, rel = SessionVWAP(), RelativeVolume(p.activity_sessions)
    bars: list[Bar] = []
    for r, h in zip(raw, ha, strict=True):
        sma.update(h)
        rsi.update(h)
        sar.update(h)
        bars.append(
            Bar(
                raw=r,
                ha=h,
                sma=h.sma,
                rsi=h.rsi,
                sar=h.sar,
                ema=ema.update(h.close),
                st_dir=st.update(h.high, h.low, h.close)[1],
                vwap=vwap.update(r),
                rel_vol=rel.update(r),
            )
        )
    for i, b in enumerate(bars):
        b.is_first = i == 0 or bars[i - 1].ts.date() != b.ts.date()
        b.is_last = i == len(bars) - 1 or bars[i + 1].ts.date() != b.ts.date()
    return bars
