"""Build the SQLite store and the Chroma policy index from data/ (TASK-005, TASK-006)."""

from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR, get_settings
from .db import apply_schema, connect, transaction
from .money import to_paise

DATA = DATA_DIR


def _load(name: str) -> list[dict]:
    return json.loads((DATA / "seed" / name).read_text(encoding="utf-8"))


def seed_sqlite() -> dict[str, int]:
    db_path = get_settings().db_path
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        try:
            if p.exists():
                p.unlink()
        except PermissionError:
            # Windows: the file is open elsewhere (UI, DB viewer). schema.sql drops and
            # recreates every table, so reseeding in place gives the same result.
            break
    conn = connect(db_path)
    try:
        apply_schema(conn)
        with transaction(conn):
            for c in _load("customers.json"):
                conn.execute(
                    "INSERT INTO customers(id,name,email,phone,city,created_at) VALUES (?,?,?,?,?,?)",
                    (c["id"], c["name"], c["email"], c["phone"], c["city"], c["created_at"]),
                )
            orders = _load("orders.json")
            for o in orders:
                conn.execute(
                    "INSERT INTO orders(id,customer_id,status,total_amount_paise,order_date,"
                    "delivered_date,refund_status) VALUES (?,?,?,?,?,?,?)",
                    (o["id"], o["customer_id"], o["status"], to_paise(o["total_amount"]),
                     o["order_date"], o["delivered_date"], o["refund_status"]),
                )
                for it in o["items"]:
                    conn.execute(
                        "INSERT INTO order_items(order_id,sku,quantity,unit_price_paise) "
                        "VALUES (?,?,?,?)",
                        (o["id"], it["sku"], it["quantity"], to_paise(it["unit_price"])),
                    )
            for i in _load("inventory.json"):
                conn.execute(
                    "INSERT INTO inventory(sku,stock,reorder_level,warehouse,updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (i["sku"], i["stock"], i["reorder_level"], i["warehouse"], i["updated_at"]),
                )
            for r in _load("refunds.json"):
                conn.execute(
                    "INSERT INTO refunds(order_id,amount_paise,reason,run_id,created_at) "
                    "VALUES (?,?,?,'seed',?)",
                    (r["order_id"], to_paise(r["amount"]), r["reason"], r["created_at"]),
                )
            for t in _load("tickets.json"):
                conn.execute(
                    "INSERT INTO tickets(customer_id,order_id,priority,status,assignee,summary,"
                    "created_at) VALUES (?,?,?,?,?,?,?)",
                    (t["customer_id"], t["order_id"], t["priority"], t["status"], t["assignee"],
                     t["summary"], t["created_at"]),
                )
            for m in _load("memory.json"):
                conn.execute(
                    "INSERT INTO memory_entries(customer_id,kind,content,run_id,created_at) "
                    "VALUES (?,?,?,'seed',?)",
                    (m["customer_id"], m["kind"], m["content"], m["created_at"]),
                )
        counts = {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608 fixed names
            for t in ("customers", "orders", "order_items", "inventory", "refunds", "tickets",
                      "memory_entries")
        }
    finally:
        conn.close()
    return counts


def seed_policies() -> int:
    from .knowledge.policy_store import build_index

    return build_index(DATA / "policies")


def seed_all(include_policies: bool = True) -> dict[str, int]:
    counts = seed_sqlite()
    if include_policies:
        counts["policy_chunks"] = seed_policies()
    return counts
