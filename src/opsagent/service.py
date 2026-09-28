"""Single entry point used by both the CLI and the Streamlit UI (TASK-016, TASK-022)."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from . import audit, failures, memory, report
from .config import ConfigError, Settings, get_settings
from .db import db_ready
from .graph.build import build_graph, sqlite_checkpointer
from .llm import LLMClient, StructuredCall
from .models import (
    ActionResult,
    ApprovalDecision,
    Decision,
    Evidence,
    InvalidState,
    Plan,
    QueryIn,
    RunNotFound,
    RunState,
    ValidationResult,
)

log = logging.getLogger("opsagent.service")


class AgentService:
    def __init__(self, settings: Settings | None = None, llm: StructuredCall | None = None,
                 checkpointer: Any = None) -> None:
        self.settings = settings or get_settings()
        if not db_ready(self.settings.db_path):
            raise ConfigError("Knowledge base not found. Run `opsagent seed` first.")
        self.llm = llm or LLMClient(self.settings)
        self.graph = build_graph(self.settings, self.llm,
                                 checkpointer or sqlite_checkpointer(self.settings))

    # -------------- helpers --------------

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}

    def _snapshot(self, run_id: str) -> RunState:
        snap = self.graph.get_state(self._config(run_id))
        v = snap.values or {}
        interrupts = [i.value for t in snap.tasks for i in (t.interrupts or [])]
        status = v.get("status", "running")
        pending: list[dict] = []
        question = None
        if interrupts:
            first = interrupts[0]
            if first.get("type") == "approval":
                status, pending = "awaiting_approval", first["pending"]
            elif first.get("type") == "clarification":
                status, question = "awaiting_clarification", first["question"]
        elif not snap.next and status == "running":
            status = "completed"
        run = audit.get_run(run_id)
        if run is None:
            raise RunNotFound(run_id)
        if status in ("awaiting_approval", "awaiting_clarification"):
            audit.set_run_status(run_id, status)
        state = RunState(
            run_id=run_id,
            status=status,
            query=run["query"],
            clarifications=v.get("clarifications") or [],
            plan=Plan.model_validate(v["plan"]) if v.get("plan") else None,
            evidence=[Evidence.model_validate(e)
                      for e in (v.get("memory_evidence") or []) + (v.get("evidence") or [])],
            decision=Decision.model_validate(v["decision"]) if v.get("decision") else None,
            validation=[ValidationResult.model_validate(x) for x in v.get("validation") or []],
            pending_approvals=pending,
            clarification_question=question,
            results=[ActionResult.model_validate(r) for r in v.get("results") or []],
            summary=v.get("summary", ""),
            error=v.get("error"),
        )
        if status in ("completed", "failed"):
            try:
                state.report_paths = report.write(state)
            except Exception as exc:  # reports must never break a run
                log.exception("report export failed: %s", exc)
            failures.clear(run_id)
        return state

    def _run(self, run_id: str, payload: Any) -> RunState:
        try:
            self.graph.invoke(payload, self._config(run_id))
        except Exception as exc:  # unexpected crash: record and surface cleanly
            log.exception("run %s crashed", run_id)
            audit.log(run_id, "run_failed", None, {"error": f"{type(exc).__name__}: {exc}"})
            audit.set_run_status(run_id, "failed")
            st = RunState(run_id=run_id, status="failed",
                          query=(audit.get_run(run_id) or {}).get("query", ""),
                          error=f"{type(exc).__name__}: {exc}")
            st.report_paths = report.write(st)
            return st
        return self._snapshot(run_id)

    def _require(self, run_id: str, status: str) -> RunState:
        if audit.get_run(run_id) is None:
            raise RunNotFound(f"Run {run_id} not found.")
        st = self._snapshot(run_id)
        if st.status != status:
            raise InvalidState(f"Run {run_id} is {st.status}, not {status}.")
        return st

    # -------------- public API --------------

    def start_run(self, query: str, inject_failure: str | None = None) -> RunState:
        q = QueryIn(query=query).query
        run_id = audit.new_run(q)
        mode = inject_failure or self.settings.inject_failure
        if mode:
            failures.parse_modes(mode)  # validate before anything runs
            failures.arm(run_id, mode)
            audit.log(run_id, "failure_mode_armed", None, {"mode": mode})
        return self._run(run_id, {"run_id": run_id, "query": q})

    def submit_approval(self, run_id: str,
                        decisions: dict[str, ApprovalDecision | dict]) -> RunState:
        st = self._require(run_id, "awaiting_approval")
        payload = {}
        for p in st.pending_approvals:
            d = decisions.get(p["action_id"])
            if d is None:
                raise InvalidState(f"Missing approval decision for {p['action_id']}.")
            d = d if isinstance(d, ApprovalDecision) else ApprovalDecision.model_validate(d)
            payload[p["action_id"]] = d.model_dump()
        return self._run(run_id, Command(resume=payload))

    def submit_clarification(self, run_id: str, answer: str) -> RunState:
        self._require(run_id, "awaiting_clarification")
        answer = QueryIn(query=answer).query
        return self._run(run_id, Command(resume=answer))

    def get_state(self, run_id: str) -> RunState:
        if audit.get_run(run_id) is None:
            raise RunNotFound(f"Run {run_id} not found.")
        return self._snapshot(run_id)


# Read-only helpers that do not need an LLM.


def list_runs(limit: int = 20) -> list[dict]:
    return audit.list_runs(limit)


def get_audit(run_id: str) -> list[dict]:
    if audit.get_run(run_id) is None:
        raise RunNotFound(f"Run {run_id} not found.")
    return audit.get_events(run_id)


def get_memory(customer_id: str) -> list[dict]:
    return memory.recent(customer_id.upper(), 50)
