"""Long-term memory stored in SQLite (TASK-024, DEC-009)."""

from __future__ import annotations

from .db import connect, now_iso


def recent(customer_id: str, n: int = 5) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, customer_id, kind, content, run_id, created_at FROM memory_entries "
            "WHERE customer_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (customer_id, max(1, int(n))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add(customer_id: str | None, kind: str, content: str, run_id: str | None) -> int:
    if kind not in ("note", "interaction_summary"):
        raise ValueError(f"invalid memory kind {kind!r}")
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO memory_entries(customer_id, kind, content, run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (customer_id, kind, content.strip(), run_id, now_iso()),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()
