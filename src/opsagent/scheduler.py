"""Dependency- and entity-aware parallel executor (TASK-020, DEC-010).

Rules:
- An action runs only after every action it depends_on has succeeded.
- Actions in the same dependency level run in parallel, except two actions on the same entity
  (e.g. order:1042) which are split into separate groups.
- If an action is rejected, invalid, failed or skipped, its dependents are skipped.
- Execution is idempotent per (run_id, action_id): a succeeded record is never re-executed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import audit, failures
from .actions.registry import get_action
from .db import connect, now_iso, transaction
from .models import ActionResult


@dataclass
class ExecItem:
    action_id: str
    tool: str
    args: dict
    entity_key: str
    depends_on: list[str] = field(default_factory=list)


def plan_groups(items: list[ExecItem]) -> list[list[ExecItem]]:
    """Topological levels, each split so no group has two items with the same entity key."""
    by_id = {i.action_id: i for i in items}
    remaining = {i.action_id: {d for d in i.depends_on if d in by_id} for i in items}
    groups: list[list[ExecItem]] = []
    done: set[str] = set()
    while remaining:
        level = [aid for aid, deps in remaining.items() if deps <= done]
        if not level:  # cycle (validator should prevent this); run the rest sequentially
            level = [next(iter(remaining))]
        level.sort(key=lambda a: list(by_id).index(a))
        pending = [by_id[a] for a in level]
        while pending:
            seen: set[str] = set()
            group, rest = [], []
            for it in pending:
                (rest if it.entity_key in seen else group).append(it)
                seen.add(it.entity_key)
            groups.append(group)
            pending = rest
        for a in level:
            remaining.pop(a)
            done.add(a)
    return groups


def upsert_record(run_id: str, action_id: str, tool: str, args: dict, risk: str, status: str,
                  result: dict | None = None, started: str | None = None,
                  ended: str | None = None) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO action_records(run_id, action_id, tool, args_json, risk, status, "
            "result_json, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, action_id) DO UPDATE SET status=excluded.status, "
            "result_json=COALESCE(excluded.result_json, result_json), "
            "started_at=COALESCE(excluded.started_at, started_at), "
            "ended_at=COALESCE(excluded.ended_at, ended_at)",
            (run_id, action_id, tool, json.dumps(args, default=str), risk, status,
             json.dumps(result, default=str) if result is not None else None, started, ended),
        )
    finally:
        conn.close()


def _existing(run_id: str, action_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT status, result_json FROM action_records WHERE run_id=? AND action_id=?",
            (run_id, action_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _set_status(run_id: str, action_id: str, status: str, result: dict | None = None,
                started: str | None = None, ended: str | None = None) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE action_records SET status=?, result_json=COALESCE(?, result_json), "
            "started_at=COALESCE(?, started_at), ended_at=COALESCE(?, ended_at) "
            "WHERE run_id=? AND action_id=?",
            (status, json.dumps(result, default=str) if result is not None else None,
             started, ended, run_id, action_id),
        )
    finally:
        conn.close()


def _execute_one(item: ExecItem, run_id: str, group_idx: int) -> ActionResult:
    prior = _existing(run_id, item.action_id)
    if prior and prior["status"] == "succeeded":  # idempotency (FR-023)
        return ActionResult(action_id=item.action_id, tool=item.tool, status="succeeded",
                            result=json.loads(prior["result_json"] or "{}"), group=group_idx)
    spec = get_action(item.tool)
    started = now_iso()
    _set_status(run_id, item.action_id, "running", started=started)
    audit.log(run_id, "action_started", "execute",
              {"action_id": item.action_id, "tool": item.tool, "args": item.args,
               "group": group_idx, "started_at": started})
    args = spec.args_model.model_validate(item.args)
    attempts, error, result = 0, None, None
    while attempts < 2:
        attempts += 1
        inject = failures.should_fail(run_id, "db_locked")
        if inject:  # log outside the write transaction (audit uses its own connection)
            audit.log(run_id, "failure_injected", "execute",
                      {"mode": "db_locked", "action_id": item.action_id})
        conn = connect()
        try:
            with transaction(conn):
                if inject:
                    raise sqlite3.OperationalError("database is locked (injected)")
                result = spec.execute(args, conn, run_id)
            error = None
            if attempts > 1:
                audit.log(run_id, "recovered", "execute",
                          {"action_id": item.action_id, "attempts": attempts})
            break
        except sqlite3.OperationalError as exc:  # transient: rolled back, retry once
            error = f"OperationalError: {exc}"
            audit.log(run_id, "retry", "execute",
                      {"action_id": item.action_id, "attempt": attempts, "error": error})
            time.sleep(0.5)
        except Exception as exc:  # business/precondition failure: do not retry
            error = f"{type(exc).__name__}: {exc}"
            break
        finally:
            conn.close()
    ended = now_iso()
    if error:
        _set_status(run_id, item.action_id, "failed", {"error": error}, ended=ended)
        audit.log(run_id, "action_failed", "execute",
                  {"action_id": item.action_id, "tool": item.tool, "error": error,
                   "attempts": attempts, "ended_at": ended})
        return ActionResult(action_id=item.action_id, tool=item.tool, status="failed",
                            error=error, group=group_idx, started_at=started, ended_at=ended,
                            attempts=attempts)
    _set_status(run_id, item.action_id, "succeeded", result, ended=ended)
    audit.log(run_id, "action_completed", "execute",
              {"action_id": item.action_id, "tool": item.tool, "result": result,
               "group": group_idx, "ended_at": ended})
    return ActionResult(action_id=item.action_id, tool=item.tool, status="succeeded",
                        result=result, group=group_idx, started_at=started, ended_at=ended,
                        attempts=attempts)


def execute(items: list[ExecItem], run_id: str,
            blocked: dict[str, str] | None = None) -> list[ActionResult]:
    """Run approved items. `blocked` maps action ids that will not run (rejected/invalid) to a
    reason; their dependents are skipped."""
    blocked = dict(blocked or {})
    results: list[ActionResult] = []
    groups = plan_groups(items)
    audit.log(run_id, "execution_plan", "execute",
              {"groups": [[f"{i.action_id}:{i.tool}" for i in g] for g in groups]})
    failed: dict[str, str] = dict(blocked)
    for gi, group in enumerate(groups, start=1):
        runnable = []
        for it in group:
            bad = [d for d in it.depends_on if d in failed]
            if bad:
                reason = f"Skipped because dependency {bad[0]} did not run ({failed[bad[0]]})."
                failed[it.action_id] = "skipped"
                _set_status(run_id, it.action_id, "skipped", {"reason": reason})
                audit.log(run_id, "action_skipped", "execute",
                          {"action_id": it.action_id, "tool": it.tool, "reason": reason})
                results.append(ActionResult(action_id=it.action_id, tool=it.tool,
                                            status="skipped", error=reason, group=gi))
            else:
                runnable.append(it)
        if not runnable:
            continue
        with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
            group_results = list(pool.map(lambda it, g=gi: _execute_one(it, run_id, g), runnable))
        for r in group_results:
            if r.status != "succeeded":
                failed[r.action_id] = r.status
        results.extend(group_results)
    return results
