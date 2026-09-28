"""Write actions (TASK-017). Simulated: they only modify the local SQLite store.

Each action has: an args model, deterministic preconditions (`check_*`) and an executor
(`do_*`) that runs inside one transaction. Business invariants live here, not in the LLM
(DEC-004). Money args are Decimal rupees with 2 decimals, stored as paise (DEC-018).
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from ..db import now_iso
from ..money import rupee_str, to_paise

ORDER_STATUS_TRANSITIONS = {
    "placed": {"packed", "cancelled"},
    "packed": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": {"returned"},
    "cancelled": set(),
    "returned": set(),
}


# ---------- argument models ----------


class RefundOrderArgs(BaseModel):
    order_id: int
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2,
                            description="Rupees with up to 2 decimals, e.g. 450.50")
    reason: str = Field(min_length=3, max_length=500)


class CancelOrderArgs(BaseModel):
    order_id: int
    reason: str = Field(min_length=3, max_length=500)


class RestockItemArgs(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=500)
    reason: str = Field(min_length=3, max_length=500)


class UpdateOrderStatusArgs(BaseModel):
    order_id: int
    status: Literal["placed", "packed", "shipped", "delivered", "cancelled", "returned"]
    note: str = Field(default="", max_length=500)


class CreateTicketArgs(BaseModel):
    customer_id: str
    order_id: int | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    summary: str = Field(min_length=3, max_length=1000)


class EscalateTicketArgs(BaseModel):
    ticket_id: int
    reason: str = Field(min_length=3, max_length=500)


class SendEmailArgs(BaseModel):
    customer_id: str
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=4000)


class AddNoteArgs(BaseModel):
    customer_id: str
    note: str = Field(min_length=3, max_length=1000)


# ---------- helpers ----------


def _order(conn: sqlite3.Connection, order_id: int):
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def _refunded(conn: sqlite3.Connection, order_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_paise), 0) FROM refunds WHERE order_id = ?", (order_id,)
    ).fetchone()[0]


def _customer_exists(conn: sqlite3.Connection, cid: str) -> bool:
    return conn.execute("SELECT 1 FROM customers WHERE id = ?", (cid.upper(),)).fetchone() is not None


# ---------- preconditions ----------


def check_refund(a: RefundOrderArgs, conn: sqlite3.Connection) -> list[str]:
    o = _order(conn, a.order_id)
    if not o:
        return [f"Order {a.order_id} does not exist."]
    errs = []
    if o["status"] in ("placed", "packed"):
        errs.append(f"Order {a.order_id} is {o['status']}; cancel it instead of refunding.")
    remaining = o["total_amount_paise"] - _refunded(conn, a.order_id)
    if to_paise(a.amount) > remaining:
        errs.append(
            f"Refund {a.amount:.2f} exceeds refundable remaining {rupee_str(remaining)} "
            f"for order {a.order_id}."
        )
    return errs


def check_cancel(a: CancelOrderArgs, conn: sqlite3.Connection) -> list[str]:
    o = _order(conn, a.order_id)
    if not o:
        return [f"Order {a.order_id} does not exist."]
    if o["status"] not in ("placed", "packed"):
        return [f"Cannot cancel order {a.order_id}: it is already {o['status']}."]
    return []


def check_restock(a: RestockItemArgs, conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT 1 FROM inventory WHERE sku = ?", (a.sku.upper(),)).fetchone()
    return [] if row else [f"SKU {a.sku} is not in inventory."]


def check_status(a: UpdateOrderStatusArgs, conn: sqlite3.Connection) -> list[str]:
    o = _order(conn, a.order_id)
    if not o:
        return [f"Order {a.order_id} does not exist."]
    if a.status not in ORDER_STATUS_TRANSITIONS[o["status"]]:
        return [f"Transition {o['status']} -> {a.status} is not allowed for order {a.order_id}."]
    return []


def check_ticket(a: CreateTicketArgs, conn: sqlite3.Connection) -> list[str]:
    errs = []
    if not _customer_exists(conn, a.customer_id):
        errs.append(f"Customer {a.customer_id} does not exist.")
    if a.order_id is not None:
        o = _order(conn, a.order_id)
        if not o:
            errs.append(f"Order {a.order_id} does not exist.")
        elif o["customer_id"] != a.customer_id.upper():
            errs.append(f"Order {a.order_id} does not belong to {a.customer_id}.")
    return errs


def check_escalate(a: EscalateTicketArgs, conn: sqlite3.Connection) -> list[str]:
    t = conn.execute("SELECT status FROM tickets WHERE id = ?", (a.ticket_id,)).fetchone()
    if not t:
        return [f"Ticket {a.ticket_id} does not exist."]
    if t["status"] == "resolved":
        return [f"Ticket {a.ticket_id} is already resolved."]
    return []


def check_customer(a: SendEmailArgs | AddNoteArgs, conn: sqlite3.Connection) -> list[str]:
    return [] if _customer_exists(conn, a.customer_id) else [f"Customer {a.customer_id} does not exist."]


# ---------- executors (called inside a transaction) ----------


def do_refund(a: RefundOrderArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    errs = check_refund(a, conn)  # re-check inside the transaction
    if errs:
        raise ValueError("; ".join(errs))
    paise = to_paise(a.amount)
    cur = conn.execute(
        "INSERT INTO refunds(order_id, amount_paise, reason, run_id, created_at) VALUES (?,?,?,?,?)",
        (a.order_id, paise, a.reason, run_id, now_iso()),
    )
    o = _order(conn, a.order_id)
    refunded = _refunded(conn, a.order_id)
    status = "full" if refunded >= o["total_amount_paise"] else "partial"
    conn.execute("UPDATE orders SET refund_status = ? WHERE id = ?", (status, a.order_id))
    return {"refund_id": cur.lastrowid, "order_id": a.order_id, "amount": rupee_str(paise),
            "order_refund_status": status,
            "refundable_remaining": rupee_str(o["total_amount_paise"] - refunded)}


def do_cancel(a: CancelOrderArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    errs = check_cancel(a, conn)
    if errs:
        raise ValueError("; ".join(errs))
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (a.order_id,))
    return {"order_id": a.order_id, "new_status": "cancelled"}


def do_restock(a: RestockItemArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    sku = a.sku.upper()
    conn.execute(
        "UPDATE inventory SET stock = stock + ?, updated_at = ? WHERE sku = ?",
        (a.quantity, now_iso(), sku),
    )
    stock = conn.execute("SELECT stock FROM inventory WHERE sku = ?", (sku,)).fetchone()[0]
    return {"sku": sku, "added": a.quantity, "new_stock": stock}


def do_status(a: UpdateOrderStatusArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    errs = check_status(a, conn)
    if errs:
        raise ValueError("; ".join(errs))
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (a.status, a.order_id))
    return {"order_id": a.order_id, "new_status": a.status}


def do_ticket(a: CreateTicketArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    cur = conn.execute(
        "INSERT INTO tickets(customer_id, order_id, priority, status, assignee, summary, created_at) "
        "VALUES (?, ?, ?, 'open', 'agent_queue', ?, ?)",
        (a.customer_id.upper(), a.order_id, a.priority, a.summary, now_iso()),
    )
    return {"ticket_id": cur.lastrowid, "priority": a.priority}


def do_escalate(a: EscalateTicketArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    conn.execute(
        "UPDATE tickets SET priority = 'urgent', assignee = 'human_queue', status = 'in_progress' "
        "WHERE id = ?",
        (a.ticket_id,),
    )
    return {"ticket_id": a.ticket_id, "priority": "urgent", "assignee": "human_queue"}


def do_email(a: SendEmailArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    cur = conn.execute(
        "INSERT INTO outbox(customer_id, subject, body, run_id, created_at) VALUES (?,?,?,?,?)",
        (a.customer_id.upper(), a.subject, a.body, run_id, now_iso()),
    )
    return {"outbox_id": cur.lastrowid, "to": a.customer_id.upper(), "subject": a.subject,
            "note": "Simulated email: written to outbox table"}


def do_note(a: AddNoteArgs, conn: sqlite3.Connection, run_id: str) -> dict:
    conn.execute(
        "INSERT INTO memory_entries(customer_id, kind, content, run_id, created_at) "
        "VALUES (?, 'note', ?, ?, ?)",
        (a.customer_id.upper(), a.note, run_id, now_iso()),
    )
    return {"customer_id": a.customer_id.upper(), "note_saved": True}

