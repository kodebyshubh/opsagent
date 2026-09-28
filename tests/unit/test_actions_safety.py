from decimal import Decimal

import pytest
from pydantic import ValidationError

from opsagent.actions.registry import ACTIONS
from opsagent.db import connect, transaction
from opsagent.models import Decision, ProposedAction
from opsagent.safety.risk import classify
from opsagent.safety.validator import validate


def _exec(name, args):
    spec = ACTIONS[name]
    a = spec.args_model.model_validate(args)
    conn = connect()
    try:
        with transaction(conn):
            return spec.execute(a, conn, "test-run")
    finally:
        conn.close()


def _check(name, args):
    spec = ACTIONS[name]
    conn = connect()
    try:
        return spec.check(spec.args_model.model_validate(args), conn)
    finally:
        conn.close()


def test_actions_succeed(env):  # TEST-020
    r = _exec("refund_order", {"order_id": 1033, "amount": "450.50", "reason": "misprint"})
    assert r["amount"] == "450.50" and r["order_refund_status"] == "full"
    conn = connect()
    assert conn.execute("SELECT amount_paise FROM refunds WHERE order_id=1033").fetchone()[0] == 45050
    conn.close()
    assert _exec("restock_item", {"sku": "SKU-TS-010", "quantity": 20, "reason": "low"})["new_stock"] == 20
    assert _exec("cancel_order", {"order_id": 1051, "reason": "changed mind"})["new_status"] == "cancelled"
    assert _exec("send_customer_email", {"customer_id": "C001", "subject": "Hello",
                                         "body": "Your refund is done."})["outbox_id"] == 1
    assert _exec("create_support_ticket", {"customer_id": "C001", "order_id": 1042,
                                           "summary": "Warranty claim"})["ticket_id"] > 0
    assert _exec("escalate_ticket", {"ticket_id": 3, "reason": "suspicious"})["assignee"] == "human_queue"
    assert _exec("update_order_status", {"order_id": 1058, "status": "delivered"})["new_status"] == "delivered"
    assert _exec("add_customer_note", {"customer_id": "C001", "note": "Repeat refunds"})["note_saved"]


def test_preconditions(env):  # TEST-021
    assert _check("refund_order", {"order_id": 1033, "amount": "450.51", "reason": "x" * 5})
    assert not _check("refund_order", {"order_id": 1033, "amount": "450.50", "reason": "x" * 5})
    assert _check("refund_order", {"order_id": 1051, "amount": "10.00", "reason": "placed order"})
    assert "already delivered" in _check("cancel_order", {"order_id": 1050, "reason": "x" * 5})[0]
    assert _check("restock_item", {"sku": "SKU-NOPE", "quantity": 1, "reason": "x" * 5})
    assert _check("update_order_status", {"order_id": 1050, "status": "placed"})
    assert _check("create_support_ticket", {"customer_id": "C002", "order_id": 1042, "summary": "abc"})
    assert _check("escalate_ticket", {"ticket_id": 1, "reason": "x" * 5})  # resolved ticket
    with pytest.raises(ValidationError):
        ACTIONS["refund_order"].args_model.model_validate(
            {"order_id": 1, "amount": "1.005", "reason": "abc"})


def test_risk_threshold_and_confidence(env):  # TEST-023
    spec = ACTIONS["refund_order"]
    at = spec.args_model(order_id=1, amount=Decimal("5000.00"), reason="abc")
    above = spec.args_model(order_id=1, amount=Decimal("5000.01"), reason="abc")
    assert classify(spec, at, 0.9, env)[:2] == ("MEDIUM", False)
    assert classify(spec, above, 0.9, env)[:2] == ("HIGH", True)
    low = ACTIONS["restock_item"]
    tier, req, reason = classify(low, low.args_model(sku="SKU-HP-001", quantity=1, reason="abc"), 0.4, env)
    assert tier == "LOW" and req and "confidence" in reason
    assert classify(ACTIONS["cancel_order"],
                    ACTIONS["cancel_order"].args_model(order_id=1051, reason="abc"), 0.99, env)[1]


def _pa(**kw):
    base = {"action_id": "a1", "tool": "restock_item",
            "args": {"sku": "SKU-HP-001", "quantity": 1, "reason": "abc"},
            "reasoning": "r", "evidence_refs": ["E1"], "confidence": 0.9, "depends_on": []}
    base.update(kw)
    return ProposedAction.model_validate(base)


def test_validator(env):  # TEST-022, TEST-SEC-02
    d = Decision(answer="x", proposed_actions=[
        _pa(),
        _pa(action_id="a2", tool="drop_database", args={}),
        _pa(action_id="a3", evidence_refs=["E99"]),
        _pa(action_id="a4", evidence_refs=[]),
        _pa(action_id="a5", depends_on=["zz"]),
        _pa(action_id="a6"),
    ])
    res = {r.action_id: r for r in validate(d, {"E1"}, env)}
    assert res["a1"].ok and res["a1"].entity_key == "sku:SKU-HP-001"
    assert "allow-list" in res["a2"].errors[0]
    assert not res["a3"].ok and not res["a4"].ok and not res["a5"].ok
    assert not res["a6"].ok and "Too many actions" in res["a6"].errors[0]


def test_validator_detects_cycle(env):
    d = Decision(answer="x", proposed_actions=[
        _pa(action_id="a1", depends_on=["a2"]), _pa(action_id="a2", depends_on=["a1"],
                                                    args={"sku": "SKU-BT-008", "quantity": 1,
                                                          "reason": "abc"})])
    assert all(not r.ok for r in validate(d, {"E1"}, env))
