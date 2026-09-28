"""Live evaluation runner over eval/scenarios.yaml (FR-030, TASK-028)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import yaml
from rich.console import Console
from rich.table import Table

from . import audit
from .config import PROJECT_ROOT
from .models import RunState

SCENARIOS = PROJECT_ROOT / "eval" / "scenarios.yaml"
RESULTS = PROJECT_ROOT / "eval" / "results"


def _drive(svc, st: RunState, approve: str, answers: list[str]) -> RunState:
    answers = list(answers)
    for _ in range(6):
        if st.status == "awaiting_clarification":
            if not answers:
                return st
            st = svc.submit_clarification(st.run_id, answers.pop(0))
        elif st.status == "awaiting_approval":
            ok = approve == "all"
            st = svc.submit_approval(st.run_id, {
                p["action_id"]: {"approve": ok, "reason": f"eval policy: approve={approve}"}
                for p in st.pending_approvals})
        else:
            return st
    return st


def check(st: RunState, expect: dict) -> tuple[bool, list[str]]:
    events = audit.get_events(st.run_id)
    executed: dict[str, int] = {}
    for r in st.results:
        if r.status == "succeeded":
            executed[r.tool] = executed.get(r.tool, 0) + 1
    approval_tools = {p["tool"] for e in events if e["event_type"] == "approval_requested"
                      for p in e["payload"]["pending"]}
    decisions = [e["payload"] for e in events if e["event_type"] == "decision_made"]
    fails: list[str] = []

    for tool in expect.get("executed", []):
        if tool not in executed:
            fails.append(f"expected {tool} to execute")
    for tool, n in (expect.get("min_executed") or {}).items():
        if executed.get(tool, 0) < n:
            fails.append(f"expected >= {n} x {tool}, got {executed.get(tool, 0)}")
    for tool in expect.get("approval_for", []):
        if tool not in approval_tools:
            fails.append(f"expected approval request for {tool}")
    for tool in expect.get("no_approval_for", []):
        if tool in approval_tools:
            fails.append(f"{tool} should not need approval")
    for tool in expect.get("forbidden", []):
        if tool in executed:
            fails.append(f"{tool} must not execute")
    if expect.get("no_actions") and executed:
        fails.append(f"expected no actions, executed {executed}")
    if expect.get("clarification") and not any(
        e["event_type"] == "clarification_requested" for e in events
    ):
        fails.append("expected a clarification question")
    if expect.get("conflict") and not any(d.get("conflicts") for d in decisions):
        fails.append("expected a conflict to be reported")
    if expect.get("memory_used"):
        mem_ids = {e.evidence_id for e in st.evidence
                   if e.found and (e.source == "memory" or e.tool == "get_refunds_for_customer")}
        cited = set()
        for d in decisions:
            cited |= set(d.get("citations") or [])
            for a in d.get("proposed_actions") or []:
                cited |= set(a.get("evidence_refs") or [])
        if not (mem_ids & cited):
            fails.append("expected customer history (memory/refunds) to be cited")
    if expect.get("not_found"):
        text = (st.decision.answer if st.decision else "").lower()
        if "not find" not in text and "no information" not in text and "not found" not in text \
                and "not available" not in text and "outside" not in text:
            fails.append("expected a 'not found in knowledge base' answer")
    if st.status == "failed":
        fails.append(f"run failed: {st.error}")
    return (not fails), fails


def run_eval(console: Console, only: list[str] | None = None) -> bool:
    from .seed import seed_sqlite
    from .service import AgentService

    scenarios = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    if only:
        scenarios = [s for s in scenarios if s["id"] in only]
    rows, passed = [], 0
    for sc in scenarios:
        seed_sqlite()  # isolate scenarios: fresh store each time
        svc = AgentService()
        t0 = time.perf_counter()
        console.print(f"[bold]{sc['id']}[/bold] {sc['title']} ...")
        try:
            st = svc.start_run(sc["query"])
            st = _drive(svc, st, sc.get("approve", "none"), sc.get("answers", []))
            ok, fails = check(st, sc.get("expect", {}))
            run_id = st.run_id
        except Exception as exc:
            ok, fails, run_id = False, [f"{type(exc).__name__}: {exc}"], "-"
        secs = round(time.perf_counter() - t0, 1)
        passed += ok
        rows.append({"id": sc["id"], "title": sc["title"], "passed": ok, "failures": fails,
                     "run_id": run_id, "seconds": secs})
        console.print(("  [green]PASS[/green]" if ok else "  [red]FAIL[/red] " + "; ".join(fails))
                      + f" [dim]({secs}s, run {run_id})[/dim]")
    t = Table(title=f"Evaluation: {passed}/{len(rows)} passed")
    for col in ("id", "scenario", "result", "run_id", "notes"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(r["id"], r["title"], "PASS" if r["passed"] else "FAIL", r["run_id"],
                  "; ".join(r["failures"]))
    console.print(t)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"eval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps({"passed": passed, "total": len(rows), "scenarios": rows},
                              indent=2), encoding="utf-8")
    console.print(f"[dim]Saved {out}[/dim]")
    return passed == len(rows)
