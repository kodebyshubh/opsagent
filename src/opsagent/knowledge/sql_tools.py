"""Read-only SQLite retrieval tools (TASK-008). All SQL is parameterized.

Each function takes a validated args model and returns JSON-safe data, or None if not found.
Money is always returned as 2-decimal rupee strings (DEC-018).
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field

from .. import memory
from ..config import get_settings
from ..db import connect
from ..money import rupee_str
from .catalog import product_name


class FindCustomerArgs(BaseModel):
    query: str = Field(min_length=1, description="Customer id (C001), name or email fragment")


class OrderArgs(BaseModel):
    order_id: int


class CustomerOrdersArgs(BaseModel):
    customer_id: str
    open_only: bool = Field(default=False, description="Only placed/packed/shipped orders")


class InventoryArgs(BaseModel):
    sku: str | None = None
    low_stock_only: bool = Field(default=False, description="Only items at/below reorder level")


class CustomerRefundsArgs(BaseModel):
    customer_id: str
    days: int = Field(default=30, ge=1, le=365)


class TicketsArgs(BaseModel):
    customer_id: str | None = None
    status: str | None = Field(default="open", description="open | in_progress | resolved | null")


class CustomerMemoryArgs(BaseModel):
    customer_id: str


def _today() -> date:
    return get_settings().today()


def find_customer(a: FindCustomerArgs) -> list[dict] | None:
    q = a.query.strip()
    like = f"%{q}%"
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, name, email, city FROM customers "
            "WHERE id = ? COLLATE NOCASE OR name LIKE ? OR email LIKE ? ORDER BY id LIMIT 5",
            (q, like, like),
        ).fetchall()
        if not rows:  # try each word (e.g. "Rahul" when given "Rahul M.")
            for word in q.split():
                if len(word) < 3:
                    continue
                rows = conn.execute(
                    "SELECT id, name, email, city FROM customers WHERE name LIKE ? LIMIT 5",
                    (f"%{word}%",),
                ).fetchall()
                if rows:
                    break
    finally:
        conn.close()
    return [dict(r) for r in rows] or None


def _order_dict(conn, row) -> dict:
    o = dict(row)
    items = conn.execute(
        "SELECT sku, quantity, unit_price_paise FROM order_items WHERE order_id = ?", (o["id"],)
    ).fetchall()
    refunded = conn.execute(
        "SELECT COALESCE(SUM(amount_paise), 0) FROM refunds WHERE order_id = ?", (o["id"],)
    ).fetchone()[0]
    total = o.pop("total_amount_paise")
    days = None
    if o.get("delivered_date"):
        days = (_today() - date.fromisoformat(o["delivered_date"])).days
    return {
        "order_id": o["id"],
        "customer_id": o["customer_id"],
        "status": o["status"],
        "order_date": o["order_date"],
        "delivered_date": o["delivered_date"],
        "days_since_delivery": days,
        "total_amount": rupee_str(total),
        "refund_status": o["refund_status"],
        "refunded_total": rupee_str(refunded),
        "refundable_remaining": rupee_str(total - refunded),
        "items": [
            {
                "sku": i["sku"],
                "name": product_name(i["sku"]),
                "quantity": i["quantity"],
                "unit_price": rupee_str(i["unit_price_paise"]),
            }
            for i in items
        ],
    }


def get_order(a: OrderArgs) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (a.order_id,)).fetchone()
        return _order_dict(conn, row) if row else None
    finally:
        conn.close()


def list_customer_orders(a: CustomerOrdersArgs) -> list[dict] | None:
    conn = connect()
    try:
        sql = "SELECT * FROM orders WHERE customer_id = ?"
        if a.open_only:
            sql += " AND status IN ('placed','packed','shipped')"
        rows = conn.execute(sql + " ORDER BY order_date DESC", (a.customer_id.upper(),)).fetchall()
        return [_order_dict(conn, r) for r in rows] or None
    finally:
        conn.close()


def get_inventory(a: InventoryArgs) -> list[dict] | None:
    conn = connect()
    try:
        if a.sku:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE sku = ?", (a.sku.strip().upper(),)
            ).fetchall()
        elif a.low_stock_only:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE stock <= reorder_level ORDER BY sku"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM inventory ORDER BY sku").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["name"] = product_name(d["sku"])
        d["below_reorder_level"] = d["stock"] <= d["reorder_level"]
        out.append(d)
    return out or None


def get_refunds_for_customer(a: CustomerRefundsArgs) -> dict:
    since = (_today() - timedelta(days=a.days)).isoformat()
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT r.id, r.order_id, r.amount_paise, r.reason, r.created_at FROM refunds r "
            "JOIN orders o ON o.id = r.order_id WHERE o.customer_id = ? AND r.created_at >= ? "
            "ORDER BY r.created_at",
            (a.customer_id.upper(), since),
        ).fetchall()
    finally:
        conn.close()
    refunds = [
        {
            "refund_id": r["id"],
            "order_id": r["order_id"],
            "amount": rupee_str(r["amount_paise"]),
            "reason": r["reason"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"customer_id": a.customer_id.upper(), "window_days": a.days,
            "refund_count": len(refunds), "refunds": refunds}


def list_tickets(a: TicketsArgs) -> list[dict] | None:
    sql = "SELECT * FROM tickets WHERE 1=1"
    params: list = []
    if a.customer_id:
        sql += " AND customer_id = ?"
        params.append(a.customer_id.upper())
    if a.status:
        sql += " AND status = ?"
        params.append(a.status)
    conn = connect()
    try:
        rows = conn.execute(sql + " ORDER BY created_at DESC LIMIT 10", params).fetchall()
    finally:
        conn.close()
    return [
        {"ticket_id": r["id"], "customer_id": r["customer_id"], "order_id": r["order_id"],
         "priority": r["priority"], "status": r["status"], "assignee": r["assignee"],
         "summary": r["summary"], "created_at": r["created_at"]}
        for r in rows
    ] or None


def get_customer_memory(a: CustomerMemoryArgs) -> list[dict] | None:
    return memory.recent(a.customer_id.upper(), 5) or None
