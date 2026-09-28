"""Deterministic validation of LLM-proposed actions (TASK-018, DEC-004).

Checks: allow-listed tool, args schema, evidence refs exist, dependency ids valid and acyclic,
preconditions against the current DB, action cap. Also assigns risk tier and approval need.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..actions.registry import get_action
from ..config import Settings
from ..db import connect
from ..models import Decision, ValidationResult
from .risk import classify


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(m) for m in graph.get(n, []) if m in graph):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in graph)


def validate(
    decision: Decision, evidence_ids: set[str], settings: Settings
) -> list[ValidationResult]:
    actions = decision.proposed_actions
    results: list[ValidationResult] = []
    ids = [a.action_id for a in actions]
    dup = {i for i in ids if ids.count(i) > 1}
    dep_graph = {a.action_id: list(a.depends_on) for a in actions}
    cyclic = _has_cycle(dep_graph)

    conn = connect()
    try:
        for idx, a in enumerate(actions):
            errs: list[str] = []
            norm: dict = dict(a.args)
            tier, requires, reason, entity = "LOW", False, "", ""
            if idx >= settings.max_actions:
                errs.append(f"Too many actions; at most {settings.max_actions} per run.")
            if a.action_id in dup:
                errs.append(f"Duplicate action_id '{a.action_id}'.")
            spec = get_action(a.tool)
            if spec is None:
                errs.append(f"Unknown action tool '{a.tool}' (not in the allow-list).")
            if not a.evidence_refs:
                errs.append("No evidence_refs given; every action must cite evidence.")
            missing = [e for e in a.evidence_refs if e not in evidence_ids]
            if missing:
                errs.append(f"evidence_refs not found in retrieved evidence: {missing}.")
            bad_deps = [d for d in a.depends_on if d not in dep_graph or d == a.action_id]
            if bad_deps:
                errs.append(f"depends_on references unknown action ids: {bad_deps}.")
            if cyclic:
                errs.append("depends_on contains a cycle.")
            if spec is not None:
                try:
                    parsed = spec.args_model.model_validate(a.args)
                    norm = parsed.model_dump(mode="json")
                    errs.extend(spec.check(parsed, conn))
                    tier, requires, reason = classify(spec, parsed, a.confidence, settings)
                    entity = spec.entity_key(parsed)
                except ValidationError as exc:
                    errs.append(
                        "Invalid args: "
                        + "; ".join(
                            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}"
                            for e in exc.errors(include_url=False)
                        )
                    )
            results.append(ValidationResult(
                action_id=a.action_id, tool=a.tool, ok=not errs, errors=errs, args=norm,
                risk=tier, requires_approval=requires, risk_reason=reason, entity_key=entity,
            ))
    finally:
        conn.close()
    return results
