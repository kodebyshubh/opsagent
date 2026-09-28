"""Typer CLI: opsagent seed | ask | chat | resume | runs | audit | memory | eval (FR-028)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import render
from .config import FAILURE_MODES, ConfigError, get_settings
from .logging_setup import setup as setup_logging
from .models import OpsAgentError, RunState

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="OpsAgent: autonomous knowledge execution agent for e-commerce ops.")

if sys.platform == "win32":  # make Rich output safe on older Windows consoles
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

console = Console()


def _service():
    from .service import AgentService

    return AgentService()


def _drive(svc, st: RunState, c: Console, approve: str, answers: list[str]) -> RunState:
    """Render a run and handle approval / clarification until it reaches a terminal state."""
    answers = list(answers)
    while True:
        render.plan(c, st)
        render.evidence(c, st)
        render.decision(c, st)
        if st.status == "awaiting_clarification":
            c.print(Panel(st.clarification_question or "", title="CLARIFICATION NEEDED",
                          border_style="yellow"))
            if answers:
                ans = answers.pop(0)
                c.print(f"[bold]Operator answer:[/bold] {ans}")
            elif approve == "ask":
                ans = Prompt.ask("Your answer")
            else:
                c.print("[yellow]No answer provided; run left awaiting clarification. "
                        f"Resume with: opsagent resume {st.run_id}[/yellow]")
                return st
            with c.status("Re-planning with your answer..."):
                st = svc.submit_clarification(st.run_id, ans)
            continue
        if st.status == "awaiting_approval":
            decisions = {}
            for p in st.pending_approvals:
                render.approval_card(c, p)
                if approve == "all":
                    decisions[p["action_id"]] = {"approve": True, "reason": "auto-approved (flag)"}
                    c.print(f"[yellow]Auto-approved {p['action_id']} (--approve all)[/yellow]")
                elif approve == "none":
                    decisions[p["action_id"]] = {"approve": False, "reason": "rejected (flag)"}
                    c.print(f"[red]Rejected {p['action_id']} (--approve none)[/red]")
                else:
                    yes = Prompt.ask(f"Approve {p['action_id']} {p['tool']}?",
                                     choices=["y", "n"], default="n") == "y"
                    reason = "" if yes else Prompt.ask("Reason for rejection", default="")
                    decisions[p["action_id"]] = {"approve": yes, "reason": reason}
            with c.status("Executing approved actions..."):
                st = svc.submit_approval(st.run_id, decisions)
            render.results(c, st)
            render.recovery(c, st.run_id)
            render.summary(c, st)
            return st
        render.results(c, st)
        render.recovery(c, st.run_id)
        render.summary(c, st)
        return st


@app.command()
def seed(policies: bool = typer.Option(True, help="Also rebuild the Chroma policy index.")):
    """Create the demo database and policy index from data/ (safe to re-run)."""
    from .seed import seed_all

    with console.status("Seeding knowledge sources..."):
        counts = seed_all(include_policies=policies)
    from .knowledge.policy_store import backend

    t = Table(title="Seeded")
    t.add_column("table")
    t.add_column("rows", justify="right")
    for k, v in counts.items():
        t.add_row(k, str(v))
    console.print(t)
    if policies:
        console.print(f"Policy embeddings backend: [bold]{backend()}[/bold]")


@app.command()
def ask(
    query: str = typer.Argument(..., help="Your request in plain English."),
    approve: str = typer.Option("ask", help="ask | all | none: how to handle approval requests."),
    answer: list[str] = typer.Option([], help="Pre-supplied clarification answer(s)."),
    inject_failure: str = typer.Option("", help=f"Demo failure mode(s), comma-separated: {', '.join(FAILURE_MODES)}"),
    transcript: Path | None = typer.Option(None, help="Save the rendered output to this file."),
    debug: bool = typer.Option(False, help="Show stack traces."),
):
    """Run the agent once on a request."""
    setup_logging(get_settings().log_level)
    c = Console(record=True, width=120) if transcript else console
    if approve not in ("ask", "all", "none"):
        raise typer.BadParameter("--approve must be ask, all or none")
    if approve == "all":
        c.print("[bold yellow]WARNING: --approve all bypasses human review "
                "(demo/eval use only).[/bold yellow]")
    try:
        svc = _service()
        c.print(Panel(query, title="OPERATOR REQUEST", border_style="white"))
        if inject_failure:
            c.print(f"[yellow]Failure injection armed: {inject_failure}[/yellow]")
        with c.status("Planning, retrieving in parallel and reasoning..."):
            st = svc.start_run(query, inject_failure=inject_failure or None)
        _drive(svc, st, c, approve, answer)
    except (ConfigError, OpsAgentError, ValueError) as exc:
        if debug:
            raise
        c.print(Panel(str(exc), title="ERROR", border_style="red"))
        raise typer.Exit(1) from None
    finally:
        if transcript:
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(c.export_text(), encoding="utf-8")
            console.print(f"[dim]Transcript saved to {transcript}[/dim]")


@app.command()
def chat():
    """Interactive loop: ask several requests in one session (type 'exit' to quit)."""
    setup_logging(get_settings().log_level)
    svc = _service()
    console.print("[bold]OpsAgent chat.[/bold] Type a request, or 'exit'.")
    while True:
        q = Prompt.ask("\n[bold cyan]operator[/bold cyan]")
        if q.strip().lower() in ("exit", "quit", ""):
            break
        try:
            with console.status("Working..."):
                st = svc.start_run(q)
            _drive(svc, st, console, "ask", [])
        except (OpsAgentError, ValueError) as exc:
            console.print(Panel(str(exc), title="ERROR", border_style="red"))


@app.command()
def resume(run_id: str, approve: str = typer.Option("ask")):
    """Continue a run that is waiting for approval or clarification (survives restarts)."""
    svc = _service()
    st = svc.get_state(run_id)
    console.print(f"Run {run_id} is [bold]{st.status}[/bold]")
    _drive(svc, st, console, approve, [])


@app.command()
def runs(limit: int = 20):
    """List recent runs."""
    from .service import list_runs

    t = Table(title="Recent runs")
    for col in ("run_id", "started", "status", "query"):
        t.add_column(col, overflow="fold")
    for r in list_runs(limit):
        t.add_row(r["run_id"], r["started_at"][:19], r["status"], r["query"][:80])
    console.print(t)


@app.command()
def audit(run: str = typer.Option(..., "--run", help="run_id"),
          full: bool = typer.Option(False, help="Show full JSON payloads.")):
    """Show the audit trail of a run."""
    from .service import get_audit

    t = Table(title=f"Audit trail {run}", expand=True)
    for col in ("#", "time (UTC)", "event", "node", "payload"):
        t.add_column(col, overflow="fold")
    for e in get_audit(run):
        payload = json.dumps(e["payload"], ensure_ascii=False, default=str)
        t.add_row(str(e["id"]), e["ts"][11:23], e["event_type"], e["node"] or "",
                  payload if full else payload[:140])
    console.print(t)


@app.command()
def memory(customer: str = typer.Option(..., "--customer", help="Customer id, e.g. C001")):
    """Show long-term memory for a customer."""
    from .service import get_memory

    rows = get_memory(customer)
    if not rows:
        console.print("No memory entries for this customer yet.")
        return
    t = Table(title=f"Memory for {customer.upper()}")
    for col in ("when", "kind", "content", "run"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(r["created_at"][:19], r["kind"], r["content"], r["run_id"] or "")
    console.print(t)


@app.command(name="eval")
def eval_cmd(only: list[str] = typer.Option([], help="Scenario ids to run, e.g. S-01")):
    """Run the live evaluation scenarios in eval/scenarios.yaml (uses the real LLM)."""
    from .evaluation import run_eval

    setup_logging(get_settings().log_level)
    ok = run_eval(console, only or None)
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()
