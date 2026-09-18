"""Kite instrument master. The CSV is public (no auth); cached once per day under data/refdata."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import requests

from zt.core import clock

URL = "https://api.kite.trade/instruments/{exchange}"


def load(exchange: str, cache_dir: Path) -> dict[str, dict]:
    """tradingsymbol -> row (instrument_token int, name, tick_size float, lot_size int, segment, instrument_type)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{exchange}_{clock.today().isoformat()}.csv"
    if not cache.exists():
        r = requests.get(URL.format(exchange=exchange), timeout=30)
        r.raise_for_status()
        cache.write_text(r.text)
    rows = {}
    for row in csv.DictReader(io.StringIO(cache.read_text())):
        row["instrument_token"] = int(row["instrument_token"])
        row["tick_size"] = float(row["tick_size"])
        row["lot_size"] = int(row["lot_size"])
        rows[row["tradingsymbol"]] = row
    return rows


def token_for(symbol: str, instruments: dict[str, dict]) -> int:
    try:
        return instruments[symbol]["instrument_token"]
    except KeyError:
        raise KeyError(f"{symbol} is not an exact NSE tradingsymbol") from None
