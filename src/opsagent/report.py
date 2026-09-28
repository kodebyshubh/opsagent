"""Per-run structured report: reports/<run_id>.json and .md (FR-032, TASK-032)."""

from __future__ import annotations

import json
from pathlib import Path

from . import audit
from .config import get_settings
from .db import now_iso
from .models import RunReport, RunState

FAILURE_EVENTS = {"failure_injected", "retry", "recovered", "tool_failed", "llm_error",
                  "structured_output_invalid", "repair_requested", "action_failed", "run_failed"}


def build(state: RunState) -> RunReport:
    events = audit.get_events(state.run_id)
    approvals = [e["payload"] for e in events if e["event_type"] == "approval_resolved"]
    return RunReport(
        run_id=state.run_id,
        generated_at=now_iso(),
        status=state.status,
        goal=state.query,
        clarifications=state.clarifications,
        plan=state.plan,
        evidence=state.evidence,
        conflicts=state.decision.conflicts if state.decision else [],
        decision=state.decision,
        validation=state.validation,
        approvals=approvals,
        results=state.results,
        failures=[{"ts": e["ts"], "event": e["event_type"], "node": e["node"], **e["payload"]}
                  for e in events if e["event_type"] in FAILURE_EVENTS],
        llm_calls=[{"ts": e["ts"], "node": e["node"], **e["payload"]}
                   for e in events if e["event_type"] == "llm_call"],
        timeline=[{"ts": e["ts"], "event": e["event_type"], "node": e["node"]} for e in events],
        summary=state.summary,
        error=state.error,
    )


def to_markdown(r: RunReport) -> str:
    out = [f"# OpsAgent run report `{r.run_id}`", "",
           f"- **Status:** {r.status}", f"- **Generated:** {r.generated_at}", "",
           "## Goal", "", r.goal, ""]
    if r.clarifications:
        out += ["## Clarifications from operator", ""] + [f"- {c}" for c in r.clarifications] + [""]
    if r.plan:
        out += ["## Plan", "", f"**Intent:** {r.plan.intent}", "", "Anticipated steps:"]
        out += [f"{i}. {s}" for i, s in enumerate(r.plan.anticipated_steps, 1)]
        out += ["", "Retrievals requested (run in parallel):"]
        out += [f"- `{q.tool}` {json.dumps(q.args)} : {q.why}" for q in r.plan.retrievals] + [""]
    if r.evidence:
        out += ["## Evidence", "", "| id | source | tool | found | latency ms |", "|---|---|---|---|---|"]
        out += [f"| {e.evidence_id} | {e.source} | {e.tool} {json.dumps(e.args, default=str)} | "
                f"{'yes' if e.found else 'no: ' + str(e.error)} | {e.latency_ms} |" for e in r.evidence]
        out.append("")
    if r.conflicts:
        out += ["## Conflicts detected", ""]
        for c in r.conflicts:
            vals = "; ".join(f"{v.value} ({v.evidence_id})" for v in c.values)
            out += [f"- **{c.field}**: {vals}. Resolution: {c.resolution}. Why: {c.justification}"]
        out.append("")
    if r.decision:
        out += ["## Decision and reasoning", "", r.decision.answer, ""]
        if r.decision.missing_info:
            out += ["Missing info: " + "; ".join(r.decision.missing_info), ""]
        for a in r.decision.proposed_actions:
            out += [f"- **{a.action_id} `{a.tool}`** {json.dumps(a.args, default=str)}",
                    f"  - reasoning: {a.reasoning}",
                    f"  - evidence: {', '.join(a.evidence_refs)}; confidence {a.confidence:.2f}; "
                    f"depends on: {', '.join(a.depends_on) or 'none'}"]
        out.append("")
    if r.validation:
        out += ["## Validation and risk", "", "| action | ok | risk | approval | notes |",
                "|---|---|---|---|---|"]
        out += [f"| {v.action_id} {v.tool} | {v.ok} | {v.risk} | {v.requires_approval} | "
                f"{'; '.join(v.errors) or v.risk_reason} |" for v in r.validation]
        out.append("")
    if r.approvals:
        out += ["## Human approvals", ""]
        for ap in r.approvals:
            out += [f"- {k}: {'APPROVED' if v['approve'] else 'REJECTED'} {v.get('reason', '')}"
                    for k, v in ap.items()]
        out.append("")
    if r.results:
        out += ["## Execution", "", "| action | status | group | detail |", "|---|---|---|---|"]
        out += [f"| {x.action_id} {x.tool} | {x.status} | {x.group or '-'} | "
                f"{json.dumps(x.result, default=str) if x.result else (x.error or '')} |"
                for x in r.results]
        out.append("")
    if r.failures:
        out += ["## Failures and recovery", ""]
        out += [f"- {f['ts']} `{f['event']}` ({f['node']}): "
                f"{json.dumps({k: v for k, v in f.items() if k not in ('ts', 'event', 'node')}, default=str)[:300]}"
                for f in r.failures]
        out.append("")
    if r.llm_calls:
        out += ["## LLM usage", ""]
        out += [f"- {c['node']}: {c.get('latency_ms')} ms, in {c.get('input_tokens')} / out "
                f"{c.get('output_tokens')} tokens" for c in r.llm_calls]
        out.append("")
    out += ["## Final summary", "", r.summary or (r.error or ""), ""]
    return "\n".join(out)


def write(state: RunState) -> list[str]:
    d: Path = get_settings().reports_dir
    d.mkdir(parents=True, exist_ok=True)
    r = build(state)
    jp, mp = d / f"{state.run_id}.json", d / f"{state.run_id}.md"
    jp.write_text(r.model_dump_json(indent=2), encoding="utf-8")
    mp.write_text(to_markdown(r), encoding="utf-8")
    return [str(jp), str(mp)]
