"""Retrieval tool registry and parallel runner (TASK-009, DEC-010)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .. import audit, failures
from ..db import now_iso
from ..models import Evidence, RetrievalRequest
from . import catalog, policy_store
from . import sql_tools as st

_PLACEHOLDER = re.compile(r"<[^<>]*>|\{\{.*?\}\}|placeholder|to be filled|TBD", re.IGNORECASE)


class SearchPoliciesArgs(BaseModel):
    query: str = Field(min_length=2, description="Natural-language search over policy documents")
    k: int = Field(default=5, ge=1, le=8)


class ProductArgs(BaseModel):
    sku: str


class FindProductsArgs(BaseModel):
    query: str = Field(min_length=2, description="Product name / category keywords")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    source: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Any], Any]


RETRIEVAL_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec("find_customer", "sqlite:customers",
                 "Look up customers by id, name or email. Returns up to 5 matches.",
                 st.FindCustomerArgs, st.find_customer),
        ToolSpec("get_order", "sqlite:orders",
                 "Full order: status, dates, days_since_delivery, total, refunds so far, "
                 "refundable_remaining, items.", st.OrderArgs, st.get_order),
        ToolSpec("list_customer_orders", "sqlite:orders",
                 "All orders of a customer (optionally only open ones).",
                 st.CustomerOrdersArgs, st.list_customer_orders),
        ToolSpec("get_inventory", "sqlite:inventory",
                 "Stock levels for one SKU, all SKUs, or only low-stock SKUs.",
                 st.InventoryArgs, st.get_inventory),
        ToolSpec("get_refunds_for_customer", "sqlite:refunds",
                 "Refunds issued to a customer within the last N days.",
                 st.CustomerRefundsArgs, st.get_refunds_for_customer),
        ToolSpec("list_tickets", "sqlite:tickets",
                 "Support tickets, filtered by customer and/or status.",
                 st.TicketsArgs, st.list_tickets),
        ToolSpec("get_customer_memory", "memory",
                 "Long-term memory: past interaction summaries and notes for a customer.",
                 st.CustomerMemoryArgs, st.get_customer_memory),
        ToolSpec("search_policies", "chroma:policies",
                 "Semantic search over company policy documents (refunds, returns, shipping, "
                 "warranty, escalation). Results include effective_date and version.",
                 SearchPoliciesArgs, lambda a: policy_store.search(a.query, a.k) or None),
        ToolSpec("get_product", "json:catalog",
                 "Catalog entry for a SKU: category, price, warranty, returnable flag, "
                 "return_window_days, vendor.", ProductArgs, lambda a: catalog.get_product(a.sku)),
        ToolSpec("find_products", "json:catalog",
                 "Search the catalog by product name or category keywords.",
                 FindProductsArgs, lambda a: catalog.find_products(a.query) or None),
    ]
}


def render_catalog() -> str:
    lines = []
    for spec in RETRIEVAL_TOOLS.values():
        props = spec.args_model.model_json_schema().get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('type', v.get('anyOf', [{}])[0].get('type', 'any'))}"
            for k, v in props.items()
        )
        lines.append(f"- {spec.name}({args}) [{spec.source}]: {spec.description}")
    return "\n".join(lines)


def _run_one(req: RetrievalRequest, evidence_id: str, run_id: str | None) -> Evidence:
    started = now_iso()
    t0 = time.perf_counter()
    spec = RETRIEVAL_TOOLS.get(req.tool)
    base = {"evidence_id": evidence_id, "tool": req.tool, "args": req.args, "started_at": started}
    if spec is None:
        return Evidence(**base, source="unknown", found=False,
                        error=f"Unknown retrieval tool '{req.tool}'", ended_at=now_iso())
    if _PLACEHOLDER.search(json.dumps(req.args, default=str)):
        return Evidence(**base, source=spec.source, found=False, ended_at=now_iso(),
                        error="Arguments contain a placeholder instead of a real value. Request "
                              "this lookup again (need_more_info) with the real value taken "
                              "from the evidence.")
    try:
        args = spec.args_model.model_validate(req.args)
    except ValidationError as exc:
        return Evidence(**base, source=spec.source, found=False,
                        error=f"Invalid args: {exc.errors(include_url=False)}", ended_at=now_iso())

    attempts = 0
    last_err: str | None = None
    data = None
    while attempts < 2:
        attempts += 1
        try:
            if failures.should_fail(run_id, "tool_timeout"):
                if run_id:
                    audit.log(run_id, "failure_injected", "retrieve",
                              {"mode": "tool_timeout", "tool": req.tool, "evidence_id": evidence_id})
                time.sleep(0.2)
                raise TimeoutError(f"{req.tool} timed out (injected)")
            data = spec.fn(args)
            last_err = None
            if attempts > 1 and run_id:
                audit.log(run_id, "recovered", "retrieve",
                          {"tool": req.tool, "evidence_id": evidence_id, "attempts": attempts})
            break
        except Exception as exc:  # tool failure: retry once, then report as missing evidence
            last_err = f"{type(exc).__name__}: {exc}"
            if run_id:
                audit.log(run_id, "retry" if attempts < 2 else "tool_failed", "retrieve",
                          {"tool": req.tool, "attempt": attempts, "error": last_err})
            time.sleep(0.3)
    latency = int((time.perf_counter() - t0) * 1000)
    if last_err:
        return Evidence(**base, source=spec.source, found=False, error=last_err,
                        latency_ms=latency, ended_at=now_iso())
    return Evidence(**base, source=spec.source, found=data is not None, data=data,
                    error=None if data is not None else "Not found",
                    latency_ms=latency, ended_at=now_iso())


def run_parallel(
    requests: list[RetrievalRequest], run_id: str | None, start_index: int = 1
) -> list[Evidence]:
    """Run retrievals concurrently; evidence ids E{n} follow request order."""
    if not requests:
        return []
    ids = [f"E{start_index + i}" for i in range(len(requests))]
    if run_id:
        audit.log(run_id, "retrieval_started", "retrieve",
                  {"count": len(requests), "parallel": True,
                   "requests": [{"id": i, "tool": r.tool, "args": r.args}
                                for i, r in zip(ids, requests, strict=True)]})
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(8, len(requests))) as pool:
        results = list(pool.map(lambda p: _run_one(p[0], p[1], run_id),
                                zip(requests, ids, strict=True)))
    total_ms = int((time.perf_counter() - t0) * 1000)
    if run_id:
        for ev in results:
            audit.log(run_id, "retrieval_completed", "retrieve",
                      {"evidence_id": ev.evidence_id, "tool": ev.tool, "found": ev.found,
                       "error": ev.error, "latency_ms": ev.latency_ms,
                       "started_at": ev.started_at, "ended_at": ev.ended_at})
        audit.log(run_id, "retrieval_batch_done", "retrieve",
                  {"wall_ms": total_ms, "sum_of_tool_ms": sum(e.latency_ms for e in results)})
    return results


def evidence_json(ev: Evidence, limit: int = 1800) -> str:
    text = json.dumps(ev.data, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"
