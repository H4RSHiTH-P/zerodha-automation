"""Tick recorder: appends every tick, in full mode, as one JSON line per tick to data/ticks/YYYY-MM-DD.ndjson."""

from __future__ import annotations

import json
import queue
import time
from datetime import datetime
from pathlib import Path

from kiteconnect import KiteTicker

from zt.core import clock


def _epoch(dt: datetime | None) -> int | None:
    return clock.to_epoch(clock.as_ist(dt)) if dt else None


def _line(recv: float, tick: dict) -> str:
    depth = tick.get("depth") or {}
    buy = (depth.get("buy") or [{}])[0]
    sell = (depth.get("sell") or [{}])[0]
    return json.dumps(
        {
            "r": round(recv, 3),
            "t": tick["instrument_token"],
            "m": tick.get("mode"),
            "x": _epoch(tick.get("exchange_timestamp")),
            "lt": _epoch(tick.get("last_trade_time")),
            "p": tick.get("last_price"),
            "v": tick.get("volume_traded"),
            "q": tick.get("last_traded_quantity"),
            "b1": [buy.get("price"), buy.get("quantity")],
            "a1": [sell.get("price"), sell.get("quantity")],
            "lc": tick.get("lower_circuit_limit"),
            "uc": tick.get("upper_circuit_limit"),
        },
        separators=(",", ":"),
    )


def record(api_key: str, access_token: str, tokens: list[int], out_dir: Path, minutes: float, status=print) -> int:
    """Blocks for `minutes`, flushing once a second. Returns the number of ticks written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{clock.today().isoformat()}.ndjson"
    q: queue.Queue = queue.Queue()
    kws = KiteTicker(api_key, access_token, reconnect=True, reconnect_max_tries=50, reconnect_max_delay=30)
    kws.on_ticks = lambda ws, ticks: q.put_nowait((time.time(), ticks))
    kws.on_connect = lambda ws, resp: (
        ws.subscribe(tokens),
        ws.set_mode(ws.MODE_FULL, tokens),
        status(f"connected, subscribed {len(tokens)} tokens"),
    )
    kws.on_error = lambda ws, code, reason: status(f"error {code}: {reason}")
    kws.on_reconnect = lambda ws, n: status(f"reconnect attempt {n}")
    kws.on_noreconnect = lambda ws: status("reconnect gave up")
    kws.connect(threaded=True)

    written = 0
    deadline = time.monotonic() + minutes * 60
    with path.open("a") as f:
        while time.monotonic() < deadline:
            try:
                recv, ticks = q.get(timeout=1.0)
            except queue.Empty:
                f.flush()
                continue
            for tick in ticks:
                f.write(_line(recv, tick) + "\n")
                written += 1
    kws.close()
    return written
