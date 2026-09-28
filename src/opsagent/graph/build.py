"""Graph wiring (TASK-014, 015, 021). See docs/IMPLEMENTATION_PLAN.md Section 2."""

from __future__ import annotations

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..llm import StructuredCall
from .nodes import MAX_CLARIFICATIONS, Nodes
from .state import AgentState


def _failed(state: dict) -> bool:
    return state.get("status") == "failed"


def route_after_plan(state: dict) -> str:
    return "respond" if _failed(state) else "retrieve"


def route_after_reason(state: dict, max_rounds: int) -> str:
    if _failed(state):
        return "respond"
    d = state.get("decision") or {}
    if d.get("clarification_question"):
        if len(state.get("clarifications") or []) >= MAX_CLARIFICATIONS:
            return "respond"
        return "ask_clarification"
    if d.get("need_more_info") and state.get("round", 0) < max_rounds:
        return "retrieve"
    if d.get("proposed_actions"):
        return "validate"
    return "respond"


def route_after_validate(state: dict) -> str:
    if state.get("validation_feedback"):
        return "reason"  # one repair round
    if state.get("pending"):
        return "approval"
    if any(v["ok"] for v in state.get("validation") or []):
        return "execute"
    return "respond"


def build_graph(settings: Settings, llm: StructuredCall, checkpointer=None):
    n = Nodes(settings, llm)
    g = StateGraph(AgentState)
    g.add_node("load_memory", n.load_memory)
    g.add_node("plan", n.plan)
    g.add_node("retrieve", n.retrieve)
    g.add_node("reason", n.reason)
    g.add_node("ask_clarification", n.ask_clarification)
    g.add_node("validate", n.validate)
    g.add_node("approval", n.approval)
    g.add_node("execute", n.execute)
    g.add_node("respond", n.respond)
    g.add_node("save_memory", n.save_memory)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "plan")
    g.add_conditional_edges("plan", route_after_plan, ["retrieve", "respond"])
    g.add_edge("retrieve", "reason")
    g.add_conditional_edges(
        "reason",
        lambda s: route_after_reason(s, settings.max_retrieval_rounds),
        ["retrieve", "ask_clarification", "validate", "respond"],
    )
    g.add_edge("ask_clarification", "plan")
    g.add_conditional_edges("validate", route_after_validate,
                            ["reason", "approval", "execute", "respond"])
    g.add_edge("approval", "execute")
    g.add_edge("execute", "respond")
    g.add_edge("respond", "save_memory")
    g.add_edge("save_memory", END)
    return g.compile(checkpointer=checkpointer)


def sqlite_checkpointer(settings: Settings) -> SqliteSaver:
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.checkpoint_path, check_same_thread=False)
    return SqliteSaver(conn)
