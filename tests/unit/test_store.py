"""History cache and persisted runs against a temporary database (no Kite)."""

from datetime import timedelta

import pytest

from tests.unit.factories import PERFORMANCE as CFG
from tests.unit.factories import random_walk
from zt.backtest import engine, filters, store
from zt.backtest.presets import PINE_V2, REALISTIC
from zt.candles.model import Interval
from zt.core import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "zt.db", check=False)
    db.migrate(c)
    return c


def test_history_cache_round_trip_and_run_persistence(conn):
    raw = random_walk(2000, 42, minutes=3, vol=0.008)
    store._insert(conn, 408065, Interval.THREE_MINUTE, raw)
    start, end = raw[0].date_time, raw[-1].date_time
    assert store.cached_range(conn, 408065, Interval.THREE_MINUTE) == (start, end)
    got = store.load_history(conn, 408065, Interval.THREE_MINUTE, start, end)  # no Kite needed
    assert [(c.date_time, c.close, c.volume) for c in got] == [(c.date_time, c.close, c.volume) for c in raw]
    with pytest.raises(LookupError):
        store.load_history(conn, 408065, Interval.THREE_MINUTE, start - timedelta(days=30), end)
    with pytest.raises(LookupError):
        store.load_history(conn, 1, Interval.THREE_MINUTE, start, end)

    result = engine.run(got, REALISTIC, 100_000.0, 5.0, None, 180)
    again = engine.run(got, REALISTIC, 100_000.0, 5.0, None, 180)
    assert result.trades == again.trades and result.trades  # deterministic
    v = filters.verdict(result, 100_000.0, CFG)
    run_id = store.save_run(conn, 408065, "SYNTH", Interval.THREE_MINUTE, result, v, 100_000.0, 5.0, None)
    assert run_id == store.save_run(conn, 408065, "SYNTH", Interval.THREE_MINUTE, result, v, 100_000.0, 5.0, None)
    assert conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0] == len(
        result.trades
    )
    other = engine.run(got, PINE_V2, 100_000.0, 5.0, None, 180)
    assert (
        store.save_run(
            conn,
            408065,
            "SYNTH",
            Interval.THREE_MINUTE,
            other,
            filters.verdict(other, 100_000.0, CFG),
            100_000.0,
            5.0,
            None,
        )
        != run_id
    )
