"""Agent state. Values are plain JSON-safe dicts so checkpoints serialize cleanly."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    query: str
    clarifications: list[str]
    memory_evidence: list[dict[str, Any]]
    plan: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    round: int
    decision: dict[str, Any] | None
    validation: list[dict[str, Any]]
    repair_count: int
    validation_feedback: str
    pending: list[dict[str, Any]]
    approvals: dict[str, dict[str, Any]]
    results: list[dict[str, Any]]
    summary: str
    status: str
    error: str | None
