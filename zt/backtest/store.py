"""History cache in the `history` table (raw Kite candles ending yesterday) and persisted runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from zt.backtest.engine import BacktestResult
from zt.backtest.filters import Verdict
from zt.candles.model import Candle, Interval
from zt.core import clock
from zt.kite import history


def cached_range(conn, token: int, interval: Interval) -> tuple[datetime, datetime] | None:
    row = conn.execute(
        "SELECT MIN(ts), MAX(ts) FROM history WHERE instrument_token=? AND interval=? AND kind='raw'",
        (token, interval.abbreviation),
    ).fetchone()
    return (clock.from_epoch(row[0]), clock.from_epoch(row[1])) if row[0] is not None else None


def _insert(conn, token: int, interval: Interval, candles: list[Candle]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany(
        "INSERT OR IGNORE INTO history(instrument_token, interval, kind, ts, open, high, low, close, volume) "
        "VALUES (?,?,'raw',?,?,?,?,?,?)",
        [
            (token, interval.abbreviation, clock.to_epoch(c.date_time), c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ],
    )
    conn.execute("COMMIT")


def load_history(conn, token: int, interval: Interval, start: datetime, end: datetime, kc=None) -> list[Candle]:
    """Candles in [start, end] from the cache, fetching only the missing head/tail through `kc` when given."""
    if end.date() >= clock.today():
        raise ValueError("history never includes today")
    have = cached_range(conn, token, interval)
    missing = []
    if have is None:
        missing.append((start, end))
    else:
        if start < have[0] - timedelta(days=1):
            missing.append((start, have[0] - timedelta(minutes=1)))
        if end > have[1] + timedelta(days=1):
            missing.append((have[1] + timedelta(minutes=1), end))
    if missing and kc is None:
        raise LookupError(f"history for {token} {interval.abbreviation} not cached and no Kite session")
    for a, b in missing:
        _insert(conn, token, interval, history.fetch(kc, token, interval, a, b))
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM history WHERE instrument_token=? AND interval=? AND kind='raw' "
        "AND ts BETWEEN ? AND ? ORDER BY ts",
        (token, interval.abbreviation, clock.to_epoch(start), clock.to_epoch(end)),
    ).fetchall()
    return [
        Candle(open=r[1], high=r[2], low=r[3], close=r[4], date_time=clock.from_epoch(r[0]), volume=r[5] or 0)
        for r in rows
    ]


def code_version() -> str:
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for f in sorted(
        list((root / "backtest").glob("*.py"))
        + list((root / "strategy").glob("*.py"))
        + [root / "candles" / "heikin_ashi.py"]
    ):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def params_hash(result: BacktestResult, equity: float, leverage: float, charges) -> str:
    blob = json.dumps(
        {
            "rules": asdict(result.rules),
            "equity": equity,
            "leverage": leverage,
            "charges": asdict(charges) if charges else None,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def save_run(
    conn,
    token: int,
    symbol: str,
    interval: Interval,
    result: BacktestResult,
    v: Verdict,
    equity: float,
    leverage: float,
    charges,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute(
        "INSERT OR IGNORE INTO backtest_runs(instrument_token, tradingsymbol, interval, fill_model, "
        "window_from, window_to, "
        "params_hash, code_version, candles, trading_days, metrics_json, score, verdict, reasons_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            token,
            symbol,
            interval.abbreviation,
            result.rules.fill,
            clock.to_epoch(result.window_from),
            clock.to_epoch(result.window_to),
            params_hash(result, equity, leverage, charges),
            code_version(),
            result.bars,
            len(result.trading_dates),
            json.dumps(v.metrics.as_dict()) if v.metrics else None,
            v.score,
            v.verdict,
            json.dumps(v.reasons),
            clock.to_epoch(clock.now()),
        ),
    )
    run_id = cur.lastrowid
    if cur.rowcount:
        conn.executemany(
            "INSERT INTO backtest_trades(run_id, n, side, qty, signal_ts, entry_ts, entry_price, stop_price, exit_ts, "
            "exit_price, exit_reason, charges, pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    t.n,
                    t.side,
                    t.qty,
                    clock.to_epoch(t.signal_ts),
                    clock.to_epoch(t.entry_ts),
                    t.entry_price,
                    t.stop_price,
                    clock.to_epoch(t.exit_ts),
                    t.exit_price,
                    t.exit_reason,
                    t.charges,
                    t.pnl,
                )
                for t in result.trades
            ],
        )
    conn.execute("COMMIT")
    return run_id
