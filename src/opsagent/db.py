"""SQLite helpers (TASK-004). One connection per call/thread, WAL mode (DEC-007)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from .config import get_settings


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else get_settings().db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files("opsagent").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def db_ready(path: Path | None = None) -> bool:
    db_path = Path(path) if path else get_settings().db_path
    if not db_path.exists():
        return False
    conn = None
    try:
        conn = connect(db_path)
        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        return bool(row[0])
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()
