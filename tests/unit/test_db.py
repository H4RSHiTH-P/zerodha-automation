import pytest

from zt.core import db


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "zt.db", check=False)  # CI runners ship older SQLite; the guard has its own test
    assert db.migrate(conn) == [1]
    assert db.migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"candles", "candle_events", "history", "backtest_runs", "signals", "positions", "kv"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_version_guard(monkeypatch):
    monkeypatch.setattr(db.sqlite3, "sqlite_version", "3.50.4")
    with pytest.raises(db.SqliteTooOld):
        db.check_version()
    monkeypatch.setattr(db.sqlite3, "sqlite_version", "3.51.3")
    db.check_version()
