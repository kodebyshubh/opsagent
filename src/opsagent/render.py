"""Rich rendering of a RunState for the CLI (kept separate from cli.py for size)."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import RunState


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def plan(c: Console, st: RunState) -> None:
    if not st.plan:
        return
    t = Table(title="Retrievals (run in parallel)", show_lines=False, expand=True)
    t.add_column("tool", style="cyan", no_wrap=True)
    t.add_column("args")
    t.add_column("why")
    for r in st.plan.retrievals:
        t.add_row(r.tool, _j(r.args), r.why)
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(st.plan.anticipated_steps, 1))
    c.print(Panel(f"[bold]Intent:[/bold] {st.plan.intent}\n\n{steps}", title="PLAN",
                  border_style="blue"))
    c.print(t)


def evidence(c: Console, st: RunState) -> None:
    if not st.evidence:
        return
    t = Table(title="EVIDENCE", expand=True)
    for col in ("id", "source", "tool / args", "found", "ms", "preview"):
        t.add_column(col, overflow="fold")
    for e in st.evidence:
        preview = _j(e.data)[:160] if e.found else f"[red]{e.error}[/red]"
        t.add_row(e.evidence_id, e.source, f"{e.tool} {_j(e.args)}",
                  "yes" if e.found else "NO", str(e.latency_ms), preview)
    c.print(t)


def decision(c: Console, st: RunState) -> None:
    d = st.decision
    if not d:
        return
    for cf in d.conflicts:
        vals = "\n".join(f"  - {v.value}  ({v.evidence_id})" for v in cf.values)
        c.print(Panel(f"[bold]{cf.field}[/bold]\n{vals}\n[bold]Resolution:[/bold] {cf.resolution}"
                      f"\n[bold]Why:[/bold] {cf.justification}", title="CONFLICT DETECTED",
                      border_style="yellow"))
    if d.missing_info:
        c.print(Panel("\n".join(f"- {m}" for m in d.missing_info), title="MISSING INFO",
                      border_style="yellow"))
    body = d.answer + (f"\n\n[dim]Citations: {', '.join(d.citations)}[/dim]" if d.citations else "")
    c.print(Panel(body, title="DECISION (reasoning before any execution)", border_style="magenta"))
    vals = {v.action_id: v for v in st.validation}
    for a in d.proposed_actions:
        v = vals.get(a.action_id)
        risk = f"[{v.risk} RISK]" if v else ""
        gate = ""
        if v and not v.ok:
            gate = "\n[red]REJECTED BY VALIDATOR:[/red] " + "; ".join(v.errors)
        elif v and v.requires_approval:
            gate = f"\n[yellow]NEEDS HUMAN APPROVAL:[/yellow] {v.risk_reason}"
        elif v:
            gate = "\n[green]AUTO-APPROVED[/green] " + v.risk_reason
        c.print(Panel(
            f"[bold]{a.tool}[/bold] {_j(v.args if v else a.args)}\n"
            f"[bold]Reasoning:[/bold] {a.reasoning}\n"
            f"Evidence: {', '.join(a.evidence_refs)} | confidence {a.confidence:.2f} | "
            f"depends on: {', '.join(a.depends_on) or 'none'}{gate}",
            title=f"ACTION {a.action_id} {risk}", border_style="cyan"))


def approval_card(c: Console, p: dict) -> None:
    c.print(Panel(
        f"[bold]{p['tool']}[/bold] {_j(p['args'])}\n"
        f"[bold]Risk:[/bold] {p['risk']} ({p['risk_reason']})\n"
        f"[bold]Reasoning:[/bold] {p['reasoning']}\n"
        f"Evidence: {', '.join(p['evidence_refs'])} | confidence {p['confidence']:.2f}",
        title=f"APPROVAL REQUIRED: {p['action_id']}", border_style="red"))


def results(c: Console, st: RunState) -> None:
    if not st.results:
        return
    t = Table(title="EXECUTION (same group = ran in parallel)", expand=True)
    for col in ("action", "tool", "group", "status", "detail"):
        t.add_column(col, overflow="fold")
    for r in sorted(st.results, key=lambda r: (r.group or 0, r.action_id)):
        style = {"succeeded": "green", "failed": "red", "skipped": "yellow",
                 "rejected": "red"}[r.status]
        detail = _j(r.result) if r.result else (r.error or "")
        t.add_row(r.action_id, r.tool, str(r.group or "-"), f"[{style}]{r.status}[/{style}]",
                  detail)
    c.print(t)


RECOVERY_EVENTS = {
    "failure_injected": "failure injected",
    "retry": "retrying",
    "recovered": "recovered",
    "tool_failed": "tool failed (reported as missing evidence)",
    "structured_output_invalid": "invalid model output",
    "repair_requested": "validator asked the model to fix its actions",
    "rate_limited": "provider rate limit, waited and retried",
    "llm_error": "LLM call error",
    "action_failed": "action failed",
}


def recovery(c: Console, run_id: str) -> None:
    """Show failures and how the agent recovered, from the audit log."""
    from . import audit

    rows = [e for e in audit.get_events(run_id) if e["event_type"] in RECOVERY_EVENTS]
    if not rows:
        return
    t = Table(title="FAILURES AND RECOVERY (from the audit log)", expand=True)
    for col in ("time", "node", "event", "detail"):
        t.add_column(col, overflow="fold")
    for e in rows:
        detail = {k: v for k, v in e["payload"].items() if k not in ("started_at", "ended_at")}
        t.add_row(e["ts"][11:23], e["node"] or "", RECOVERY_EVENTS[e["event_type"]],
                  _j(detail)[:160])
    c.print(t)


def summary(c: Console, st: RunState) -> None:
    if st.status == "failed":
        c.print(Panel(st.error or st.summary, title=f"RUN FAILED  run_id={st.run_id}",
                      border_style="red"))
        return
    c.print(Panel(st.summary or "(no summary)", title=f"SUMMARY  run_id={st.run_id}",
                  border_style="green"))
    if st.report_paths:
        c.print(f"[dim]Report: {' , '.join(st.report_paths)}[/dim]")
