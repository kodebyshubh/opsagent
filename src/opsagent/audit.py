"""Append-only audit log plus run tracking (TASK-013, DEC-016).

audit_events rows are only ever INSERTed. runs.status is the only mutable field.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .db import connect, now_iso

log_ = logging.getLogger("opsagent.audit")


def _json(payload: Any) -> str:
    def default(o: Any) -> Any:
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        return str(o)

    return json.dumps(payload, default=default, ensure_ascii=False)


def new_run(query: str, parent_run_id: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO runs(run_id, parent_run_id, query, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (run_id, parent_run_id, query, now_iso()),
        )
    finally:
        conn.close()
    log(run_id, "run_started", None, {"query": query})
    return run_id


def set_run_status(run_id: str, status: str) -> None:
    terminal = status in ("completed", "failed")
    conn = connect()
    try:
        conn.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ?",
            (status, now_iso() if terminal else None, run_id),
        )
    finally:
        conn.close()


def log(run_id: str, event_type: str, node: str | None, payload: Any = None) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO audit_events(run_id, ts, event_type, node, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, now_iso(), event_type, node, _json(payload or {})),
        )
    finally:
        conn.close()
    log_.info("run=%s event=%s node=%s", run_id, event_type, node)


def get_run(run_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_events(run_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, run_id, ts, event_type, node, payload_json FROM audit_events "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out
