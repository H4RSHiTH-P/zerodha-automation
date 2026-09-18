"""Zerodha intraday charges, extracted from the two inline blocks in performance.py (lines 296-308, 339-351)."""

from __future__ import annotations

from zt.config import Charges


def leg(amount: float, is_sell: bool, c: Charges) -> float:
    """Charges for one executed order of `amount` rupees. STT on the sell leg, stamp duty on the buy leg."""
    brokerage = min(c.max_brokerage, amount * c.brokerage)
    stt = amount * c.stt if is_sell else 0
    nse = amount * c.nse
    sebi = amount * c.sebi
    gst = (brokerage + nse + sebi) * c.gst
    stamp_duty = amount * c.stamp_duty if not is_sell else 0
    return brokerage + stt + nse + sebi + gst + stamp_duty


def round_trip(side: int, entry_amount: float, exit_amount: float, c: Charges) -> float:
    """side +1 long (buy then sell), -1 short (sell then buy)."""
    return leg(entry_amount, is_sell=(side < 0), c=c) + leg(exit_amount, is_sell=(side > 0), c=c)
