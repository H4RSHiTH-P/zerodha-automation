"""SQLite access: version guard, connection pragmas, migrations."""

from __future__ import annotations

import re
from pathlib import Path

try:
    import pysqlite3 as sqlite3  # Linux wheels ship a current SQLite
except ImportError:
    import sqlite3

MIN_SQLITE = (3, 51, 3)  # WAL-reset corruption with several processes fixed in 3.51.3
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class SqliteTooOld(RuntimeError):
    pass


def sqlite_version() -> tuple[int, ...]:
    return tuple(int(x) for x in sqlite3.sqlite_version.split("."))


def check_version() -> None:
    if sqlite_version() < MIN_SQLITE:
        raise SqliteTooOld(f"SQLite {sqlite3.sqlite_version} < {'.'.join(map(str, MIN_SQLITE))}")


def connect(path: str | Path, check: bool = True) -> sqlite3.Connection:
    """One connection per thread. Autocommit mode: writers issue BEGIN IMMEDIATE themselves."""
    if check:
        check_version()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(v) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply every NNN_*.sql above the current version, each in its own transaction. Idempotent."""
    current = schema_version(conn)
    applied = []
    for file in sorted(migrations_dir.glob("*.sql")):
        m = re.match(r"(\d+)_", file.name)
        if not m:
            continue
        v = int(m.group(1))
        if v <= current:
            continue
        conn.executescript(
            f"BEGIN IMMEDIATE;\n{file.read_text()}\nINSERT INTO schema_version(v) VALUES ({v});\nCOMMIT;"
        )
        applied.append(v)
    return applied
