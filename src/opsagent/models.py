"""All Pydantic models: LLM contracts, evidence, validation, run state (TASK-007)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskTier = Literal["LOW", "MEDIUM", "HIGH"]
RunStatus = Literal[
    "running", "awaiting_approval", "awaiting_clarification", "completed", "failed"
]


class OpsAgentError(RuntimeError):
    pass


class LLMUnavailableError(OpsAgentError):
    pass


class StructuredOutputError(OpsAgentError):
    pass


class RunNotFound(OpsAgentError):
    pass


class InvalidState(OpsAgentError):
    pass


# ---------- LLM contracts ----------


class RetrievalRequest(BaseModel):
    tool: str = Field(description="Name of a retrieval tool from the catalog")
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = Field(default="", description="Why this retrieval is needed")


class Plan(BaseModel):
    """Output of the planner node."""

    intent: str = Field(description="One-sentence restatement of what the operator wants")
    retrievals: list[RetrievalRequest] = Field(default_factory=list)
    anticipated_steps: list[str] = Field(
        default_factory=list, description="Ordered high-level steps the agent expects to take"
    )


class ConflictValue(BaseModel):
    value: str
    evidence_id: str


class Conflict(BaseModel):
    field: str = Field(description="What the sources disagree about")
    values: list[ConflictValue]
    resolution: str = Field(description="Which value is used")
    justification: str = Field(description="Why that value wins")


class ProposedAction(BaseModel):
    action_id: str = Field(description="Short unique id like a1, a2")
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    depends_on: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """Output of the reasoner node."""

    answer: str = Field(description="Grounded answer / explanation for the operator")
    citations: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    need_more_info: list[RetrievalRequest] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    summary_for_memory: str = ""

    @field_validator("clarification_question")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v if v and v.strip() else None


# ---------- evidence, validation, execution ----------


class Evidence(BaseModel):
    evidence_id: str
    source: str  # sqlite:orders | chroma:policies | json:catalog | memory
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    found: bool = True
    data: Any = None
    error: str | None = None
    latency_ms: int = 0
    started_at: str | None = None
    ended_at: str | None = None


class ValidationResult(BaseModel):
    action_id: str
    tool: str
    ok: bool
    errors: list[str] = Field(default_factory=list)
    args: dict[str, Any] = Field(default_factory=dict)  # normalized, JSON-safe
    risk: RiskTier = "LOW"
    requires_approval: bool = False
    risk_reason: str = ""
    entity_key: str = ""


class ApprovalDecision(BaseModel):
    approve: bool
    reason: str = ""


class ActionResult(BaseModel):
    action_id: str
    tool: str
    status: Literal["succeeded", "failed", "skipped", "rejected"]
    result: dict[str, Any] | None = None
    error: str | None = None
    group: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    attempts: int = 0


class QueryIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)


class RunState(BaseModel):
    """What service.py returns to the CLI and UI."""

    run_id: str
    status: RunStatus
    query: str
    clarifications: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    decision: Decision | None = None
    validation: list[ValidationResult] = Field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    clarification_question: str | None = None
    results: list[ActionResult] = Field(default_factory=list)
    summary: str = ""
    error: str | None = None
    report_paths: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    """Structured per-run report written to reports/<run_id>.json (FR-032)."""

    run_id: str
    generated_at: str
    status: RunStatus
    goal: str
    clarifications: list[str]
    plan: Plan | None
    evidence: list[Evidence]
    conflicts: list[Conflict]
    decision: Decision | None
    validation: list[ValidationResult]
    approvals: list[dict[str, Any]]
    results: list[ActionResult]
    failures: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    summary: str
    error: str | None = None
