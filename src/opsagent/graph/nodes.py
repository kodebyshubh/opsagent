"""Graph nodes (TASK-014, 015, 021, 025). One method per node; every node writes audit events.

The LLM fills Plan and Decision objects. Everything else here is deterministic.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from string import Template
from typing import Any

from langgraph.types import interrupt

from .. import audit, memory, scheduler
from ..actions import registry as action_registry
from ..config import Settings
from ..knowledge import registry as retrieval_registry
from ..llm import StructuredCall
from ..models import (
    ActionResult,
    Decision,
    Evidence,
    LLMUnavailableError,
    Plan,
    RetrievalRequest,
    StructuredOutputError,
)
from ..safety.validator import validate as validate_actions

MAX_CLARIFICATIONS = 3
_CUSTOMER_ID = re.compile(r"\bC\d{3}\b", re.IGNORECASE)


def _prompt(name: str) -> Template:
    return Template(
        resources.files("opsagent").joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    )


def render_evidence(items: list[dict[str, Any]]) -> str:
    blocks = []
    for raw in items:
        ev = Evidence.model_validate(raw)
        attrs = (f'id="{ev.evidence_id}" source="{ev.source}" tool="{ev.tool}" '
                 f"args='{json.dumps(ev.args, default=str)}'")
        if ev.found:
            body = retrieval_registry.evidence_json(ev)
        else:
            body = f"NOT FOUND / ERROR: {ev.error}"
        blocks.append(f"<evidence {attrs}>\n{body}\n</evidence>")
    notices = version_notices(items)
    if notices:
        blocks.append("VERSION NOTICE (from document metadata):\n" + "\n".join(notices))
    return "\n".join(blocks) if blocks else "(no evidence retrieved)"


def version_notices(items: list[dict[str, Any]]) -> list[str]:
    """Point out superseded policy versions present in the evidence (metadata, not a verdict)."""
    notes = []
    for raw in items:
        if raw.get("tool") != "search_policies" or not isinstance(raw.get("data"), list):
            continue
        for hit in raw["data"]:
            if isinstance(hit, dict) and hit.get("superseded_by"):
                notes.append(
                    f"- {raw['evidence_id']} contains {hit['source']} (effective "
                    f"{hit.get('effective_date')}), which is superseded by {hit['superseded_by']}. "
                    "Compare what the two versions say on this topic and list every difference "
                    "in \"conflicts\" with your resolution."
                )
    return sorted(set(notes))


class Nodes:
    def __init__(self, settings: Settings, llm: StructuredCall) -> None:
        self.s = settings
        self.llm = llm

    # ---------------- helpers ----------------

    def _request_text(self, state: dict) -> str:
        text = f"OPERATOR REQUEST:\n{state['query']}"
        for i, c in enumerate(state.get("clarifications") or [], start=1):
            text += f"\n\nCLARIFICATION {i} FROM OPERATOR:\n{c}"
        return text

    def _fail(self, state: dict, node: str, exc: Exception) -> dict:
        msg = str(exc)
        if isinstance(exc, LLMUnavailableError):
            msg = "LLM provider unavailable. No actions were executed. " + msg
        audit.log(state["run_id"], "run_failed", node, {"error": msg})
        return {"status": "failed", "error": msg}

    # ---------------- nodes ----------------

    def load_memory(self, state: dict) -> dict:
        ids = sorted({m.upper() for m in _CUSTOMER_ID.findall(state["query"])})
        items = []
        for cid in ids:
            rows = memory.recent(cid, 5)
            if rows:
                items.append(Evidence(
                    evidence_id=f"M{len(items) + 1}", source="memory", tool="get_customer_memory",
                    args={"customer_id": cid}, data=rows,
                ).model_dump(mode="json"))
        audit.log(state["run_id"], "memory_loaded", "load_memory",
                  {"customer_ids": ids, "entries": sum(len(i["data"]) for i in items)})
        return {"memory_evidence": items, "round": 0, "repair_count": 0,
                "clarifications": state.get("clarifications") or [], "evidence": [],
                "status": "running"}

    def plan(self, state: dict) -> dict:
        system = _prompt("planner").substitute(
            today=self.s.today().isoformat(),
            retrieval_catalog=retrieval_registry.render_catalog(),
            action_catalog=action_registry.render_catalog(),
        )
        user = self._request_text(state)
        mem = state.get("memory_evidence") or []
        if mem:
            user += "\n\nALREADY LOADED LONG-TERM MEMORY:\n" + render_evidence(mem)
        if state.get("evidence"):
            user += "\n\nEVIDENCE ALREADY RETRIEVED (do not fetch again):\n" + "\n".join(
                f"- {e['evidence_id']}: {e['tool']} {json.dumps(e['args'], default=str)}"
                for e in state["evidence"]
            )
        try:
            plan = self.llm(Plan, system, user, run_id=state["run_id"], node="plan")
        except (LLMUnavailableError, StructuredOutputError) as exc:
            return self._fail(state, "plan", exc)
        audit.log(state["run_id"], "plan_created", "plan", plan.model_dump(mode="json"))
        return {"plan": plan.model_dump(mode="json"), "round": 0}

    def retrieve(self, state: dict) -> dict:
        decision = state.get("decision")
        if state.get("round", 0) == 0 or not decision:
            reqs = (state.get("plan") or {}).get("retrievals", [])
        else:
            reqs = decision.get("need_more_info", [])
        requests = [RetrievalRequest.model_validate(r) for r in reqs][:8]
        existing = state.get("evidence") or []
        new = retrieval_registry.run_parallel(requests, state["run_id"], start_index=len(existing) + 1)
        return {"evidence": existing + [e.model_dump(mode="json") for e in new],
                "round": state.get("round", 0) + 1}

    def reason(self, state: dict) -> dict:
        rnd, max_rounds = state.get("round", 1), self.s.max_retrieval_rounds
        feedback = state.get("validation_feedback") or ""
        if feedback:
            feedback = (
                "\nVALIDATION FEEDBACK ON YOUR PREVIOUS PROPOSAL (fix these problems; do not "
                "repeat invalid actions):\n" + feedback + "\n"
            )
        system = _prompt("reasoner").substitute(
            today=self.s.today().isoformat(), round=rnd, max_rounds=max_rounds,
            action_catalog=action_registry.render_catalog(),
            retrieval_catalog=(retrieval_registry.render_catalog() if rnd < max_rounds
                               else "(no more retrieval rounds allowed)"),
            validation_feedback=feedback,
        )
        user = (
            self._request_text(state)
            + "\n\nEVIDENCE:\n"
            + render_evidence((state.get("memory_evidence") or []) + (state.get("evidence") or []))
        )
        if rnd >= max_rounds:
            user += "\n\nNOTE: no more retrieval rounds are allowed; decide with this evidence."
        try:
            decision = self.llm(Decision, system, user, run_id=state["run_id"], node="reason")
        except (LLMUnavailableError, StructuredOutputError) as exc:
            return self._fail(state, "reason", exc)
        if rnd >= max_rounds:
            decision.need_more_info = []
        if decision.clarification_question:
            decision.proposed_actions = []
            decision.need_more_info = []
        d = decision.model_dump(mode="json")
        audit.log(state["run_id"], "decision_made", "reason", d)
        if decision.clarification_question:
            audit.log(state["run_id"], "clarification_requested", "reason",
                      {"question": decision.clarification_question,
                       "missing_info": decision.missing_info})
        return {"decision": d, "validation_feedback": ""}

    def ask_clarification(self, state: dict) -> dict:
        question = state["decision"]["clarification_question"]
        answer = interrupt({"type": "clarification", "question": question})
        answer = str(answer).strip()
        audit.log(state["run_id"], "clarification_answered", "ask_clarification",
                  {"question": question, "answer": answer})
        return {"clarifications": (state.get("clarifications") or []) + [answer],
                "decision": None, "round": 0}

    def validate(self, state: dict) -> dict:
        decision = Decision.model_validate(state["decision"])
        ids = {e["evidence_id"] for e in (state.get("evidence") or [])}
        ids |= {e["evidence_id"] for e in (state.get("memory_evidence") or [])}
        results = validate_actions(decision, ids, self.s)
        run_id = state["run_id"]
        for r in results:
            audit.log(run_id, "validation_result", "validate", r.model_dump(mode="json"))
        invalid = [r for r in results if not r.ok]
        if invalid and state.get("repair_count", 0) < 1:
            fb = "\n".join(f"- {r.action_id} ({r.tool}): {'; '.join(r.errors)}" for r in invalid)
            audit.log(run_id, "repair_requested", "validate", {"feedback": fb})
            return {"validation": [r.model_dump(mode="json") for r in results],
                    "validation_feedback": fb, "repair_count": state.get("repair_count", 0) + 1,
                    "pending": []}
        pending = []
        by_id = {a.action_id: a for a in decision.proposed_actions}
        for r in results:
            status = "invalid" if not r.ok else (
                "awaiting_approval" if r.requires_approval else "approved")
            scheduler.upsert_record(run_id, r.action_id, r.tool, r.args, r.risk, status,
                                    {"errors": r.errors} if not r.ok else None)
            if r.ok and r.requires_approval:
                a = by_id[r.action_id]
                pending.append({
                    "action_id": r.action_id, "tool": r.tool, "args": r.args, "risk": r.risk,
                    "risk_reason": r.risk_reason, "reasoning": a.reasoning,
                    "confidence": a.confidence, "evidence_refs": a.evidence_refs,
                    "depends_on": a.depends_on,
                })
        if pending:
            audit.log(run_id, "approval_requested", "validate", {"pending": pending})
            audit.set_run_status(run_id, "awaiting_approval")
        return {"validation": [r.model_dump(mode="json") for r in results], "pending": pending,
                "validation_feedback": ""}

    def approval(self, state: dict) -> dict:
        response = interrupt({"type": "approval", "pending": state["pending"]})
        response = response or {}
        approvals: dict[str, dict] = {}
        for p in state["pending"]:
            raw = response.get(p["action_id"]) or {"approve": False, "reason": "no decision given"}
            approve = bool(raw.get("approve"))
            approvals[p["action_id"]] = {"approve": approve, "reason": raw.get("reason", "")}
            scheduler.upsert_record(state["run_id"], p["action_id"], p["tool"], p["args"],
                                    p["risk"], "approved" if approve else "rejected")
        audit.log(state["run_id"], "approval_resolved", "approval", approvals)
        audit.set_run_status(state["run_id"], "running")
        return {"approvals": approvals}

    def execute(self, state: dict) -> dict:
        decision = Decision.model_validate(state["decision"])
        vals = {v["action_id"]: v for v in state.get("validation") or []}
        approvals = state.get("approvals") or {}
        items, blocked, pre_results = [], {}, []
        for a in decision.proposed_actions:
            v = vals.get(a.action_id)
            if not v or not v["ok"]:
                blocked[a.action_id] = "invalid"
                continue
            if v["requires_approval"] and not approvals.get(a.action_id, {}).get("approve"):
                reason = approvals.get(a.action_id, {}).get("reason") or "rejected by operator"
                blocked[a.action_id] = "rejected"
                pre_results.append(ActionResult(action_id=a.action_id, tool=a.tool,
                                                status="rejected", error=reason))
                continue
            items.append(scheduler.ExecItem(a.action_id, a.tool, v["args"], v["entity_key"],
                                            list(a.depends_on)))
        results = pre_results + (scheduler.execute(items, state["run_id"], blocked) if items or
                                 blocked else [])
        return {"results": [r.model_dump(mode="json") for r in results]}

    def respond(self, state: dict) -> dict:
        run_id = state["run_id"]
        if state.get("status") == "failed":
            summary = f"Run failed: {state.get('error')}"
            audit.set_run_status(run_id, "failed")
            return {"summary": summary}
        d = state.get("decision") or {}
        lines = [d.get("answer", "").strip()]
        results = state.get("results") or []
        invalid = [v for v in state.get("validation") or [] if not v["ok"]]
        if results or invalid:
            lines.append("")
            lines.append("Actions:")
            for r in results:
                detail = r.get("result") if r["status"] == "succeeded" else r.get("error")
                lines.append(f"- {r['action_id']} {r['tool']}: {r['status'].upper()} "
                             f"{json.dumps(detail, default=str) if detail else ''}".rstrip())
            for v in invalid:
                lines.append(f"- {v['action_id']} {v['tool']}: NOT EXECUTED (failed validation: "
                             f"{'; '.join(v['errors'])})")
        summary = "\n".join(lines).strip()
        audit.log(run_id, "run_completed", "respond",
                  {"summary": summary, "actions": len(results)})
        audit.set_run_status(run_id, "completed")
        return {"summary": summary, "status": "completed"}

    def save_memory(self, state: dict) -> dict:
        if state.get("status") != "completed":
            return {}
        d = state.get("decision") or {}
        customers: set[str] = set()
        for e in state.get("evidence") or []:
            data = e.get("data")
            rows = data if isinstance(data, list) else [data]
            if e["tool"] == "find_customer" and isinstance(data, list) and len(data) != 1:
                continue  # ambiguous name lookup
            for row in rows:
                if isinstance(row, dict):
                    cid = row.get("customer_id") or (row.get("id") if e["tool"] == "find_customer"
                                                     else None)
                    if cid and _CUSTOMER_ID.fullmatch(str(cid)):
                        customers.add(str(cid).upper())
        if len(customers) > 3:  # broad queries (e.g. stock reports) are not customer memories
            return {}
        outcome = ", ".join(f"{r['tool']} {r['status']}" for r in state.get("results") or [])
        text = (d.get("summary_for_memory") or "").strip()
        if outcome:
            text = f"{text} Outcome: {outcome}.".strip()
        if not text:
            return {}
        for cid in sorted(customers):
            memory.add(cid, "interaction_summary", text, state["run_id"])
        if customers:
            audit.log(state["run_id"], "memory_saved", "save_memory",
                      {"customer_ids": sorted(customers), "content": text})
        return {}
