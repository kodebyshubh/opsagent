"""Action registry: args model, preconditions, executor, entity key, risk (TASK-017, DEC-015)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ..config import Settings
from ..money import format_inr, to_paise
from . import tools as t

RiskFn = Callable[[Any, Settings], tuple[str, str]]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    check: Callable[[Any, Any], list[str]]
    execute: Callable[[Any, Any, str], dict]
    entity_key: Callable[[Any], str]
    risk: RiskFn
    risk_note: str


def _refund_risk(a: t.RefundOrderArgs, s: Settings) -> tuple[str, str]:
    if to_paise(a.amount) > to_paise(s.refund_approval_threshold):
        return "HIGH", (
            f"Refund {format_inr(a.amount)} is above the "
            f"{format_inr(s.refund_approval_threshold)} approval threshold."
        )
    return "MEDIUM", "Refund within auto-approval limit."


def _fixed(tier: str, reason: str) -> RiskFn:
    return lambda a, s: (tier, reason)


ACTIONS: dict[str, ActionSpec] = {
    a.name: a
    for a in [
        ActionSpec("refund_order", "Issue a (partial or full) refund for an order.",
                   t.RefundOrderArgs, t.check_refund, t.do_refund,
                   lambda a: f"order:{a.order_id}", _refund_risk,
                   "HIGH above the refund approval threshold, else MEDIUM"),
        ActionSpec("cancel_order", "Cancel an order that is still placed or packed.",
                   t.CancelOrderArgs, t.check_cancel, t.do_cancel,
                   lambda a: f"order:{a.order_id}",
                   _fixed("HIGH", "Cancelling an order is irreversible."), "always HIGH"),
        ActionSpec("update_order_status", "Move an order to the next allowed status.",
                   t.UpdateOrderStatusArgs, t.check_status, t.do_status,
                   lambda a: f"order:{a.order_id}",
                   _fixed("MEDIUM", "Changes order state."), "MEDIUM"),
        ActionSpec("restock_item", "Add units to inventory for a SKU.",
                   t.RestockItemArgs, t.check_restock, t.do_restock,
                   lambda a: f"sku:{a.sku.upper()}",
                   _fixed("LOW", "Inventory increase is low risk."), "LOW"),
        ActionSpec("create_support_ticket", "Open a support ticket for a customer.",
                   t.CreateTicketArgs, t.check_ticket, t.do_ticket,
                   lambda a: f"customer:{a.customer_id.upper()}:ticket",
                   _fixed("LOW", "Creates a ticket only."), "LOW"),
        ActionSpec("escalate_ticket", "Escalate a ticket to the human queue with urgent priority.",
                   t.EscalateTicketArgs, t.check_escalate, t.do_escalate,
                   lambda a: f"ticket:{a.ticket_id}",
                   _fixed("LOW", "Routes work to a human."), "LOW"),
        ActionSpec("send_customer_email", "Send an email to a customer (simulated via outbox).",
                   t.SendEmailArgs, t.check_customer, t.do_email,
                   lambda a: f"customer:{a.customer_id.upper()}:email",
                   _fixed("MEDIUM", "Customer-facing message (auto-executes, DEC-015)."),
                   "MEDIUM, auto-executes"),
        ActionSpec("add_customer_note", "Save a note to the customer's long-term memory.",
                   t.AddNoteArgs, t.check_customer, t.do_note,
                   lambda a: f"customer:{a.customer_id.upper()}:note",
                   _fixed("LOW", "Internal note."), "LOW"),
    ]
}


def get_action(name: str) -> ActionSpec | None:
    return ACTIONS.get(name)


def render_catalog() -> str:
    lines = []
    for spec in ACTIONS.values():
        props = spec.args_model.model_json_schema().get("properties", {})
        args = ", ".join(props.keys())
        lines.append(f"- {spec.name}({args}): {spec.description} Risk: {spec.risk_note}.")
    return "\n".join(lines)
