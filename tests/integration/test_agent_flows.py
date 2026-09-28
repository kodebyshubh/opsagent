import copy
import json
from pathlib import Path

from opsagent import audit, memory
from opsagent.db import connect
from opsagent.service import AgentService
from tests import scripts as S
from tests.fakes import FakeLLM


def svc_with(script):
    return AgentService(llm=FakeLLM(script))


def kinds(run_id):
    return [e["event_type"] for e in audit.get_events(run_id)]


def q(sql):
    c = connect()
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_s01_approval_parallel_audit_report(env):  # TEST-028
    svc = svc_with({"Plan": [S.S01_PLAN], "Decision": [S.S01_DECISION]})
    st = svc.start_run("Rahul wants a refund for 1042, restock it, email him")
    assert st.status == "awaiting_approval"
    assert [p["tool"] for p in st.pending_approvals] == ["refund_order"]
    assert not q("SELECT * FROM refunds WHERE order_id=1042")  # nothing before approval
    st = svc.submit_approval(st.run_id, {"a1": {"approve": True}})
    assert st.status == "completed"
    assert {r.tool: r.status for r in st.results} == {
        "refund_order": "succeeded", "restock_item": "succeeded", "send_customer_email": "succeeded"}
    groups = {r.tool: r.group for r in st.results}
    assert groups["restock_item"] == groups["send_customer_email"] != groups["refund_order"]
    assert q("SELECT amount_paise FROM refunds WHERE order_id=1042")[0][0] == 649900
    k = kinds(st.run_id)
    order = ["plan_created", "retrieval_started", "decision_made", "validation_result",
             "approval_requested", "approval_resolved", "action_started", "action_completed",
             "run_completed", "memory_saved"]
    firsts = [k.index(x) for x in order]
    assert firsts == sorted(firsts)
    assert k.index("decision_made") < k.index("action_started")  # reasoning before execution
    js = json.loads(Path(st.report_paths[0]).read_text(encoding="utf-8"))  # TEST-062
    assert js["status"] == "completed" and js["conflicts"]
    assert "## Execution" in Path(st.report_paths[1]).read_text(encoding="utf-8")


def test_s01_rejected_skips_dependents(env):  # FR-021
    svc = svc_with({"Plan": [S.S01_PLAN], "Decision": [S.S01_DECISION]})
    st = svc.start_run("refund 1042")
    st = svc.submit_approval(st.run_id, {"a1": {"approve": False, "reason": "needs manager"}})
    res = {r.tool: r.status for r in st.results}
    assert res == {"refund_order": "rejected", "restock_item": "skipped",
                   "send_customer_email": "skipped"}
    assert not q("SELECT * FROM refunds WHERE order_id=1042")


def test_answer_only(env):  # TEST-016
    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    st = svc.start_run("warranty on SKU-HP-001?")
    assert st.status == "completed" and not st.results and "12 months" in st.summary
    assert st.evidence[0].data["warranty_months"] == 12


def test_clarification_then_cancel(env):  # TEST-017
    svc = svc_with({"Plan": [S.S05_PLAN, S.S05_PLAN2],
                    "Decision": [S.S05_CLARIFY, S.S05_DECISION]})
    st = svc.start_run("Cancel Priya's order")
    assert st.status == "awaiting_clarification" and "1051" in st.clarification_question
    assert not st.results
    st = svc.submit_clarification(st.run_id, "1063")
    assert st.status == "awaiting_approval"  # cancel is HIGH risk
    st = svc.submit_approval(st.run_id, {"a1": {"approve": True}})
    assert st.results[0].status == "succeeded"
    assert q("SELECT status FROM orders WHERE id=1063")[0][0] == "cancelled"
    assert st.clarifications == ["1063"]


def test_need_more_info_round_cap(env):  # TEST-018
    more = {"answer": "need more", "need_more_info": [
        {"tool": "get_order", "args": {"order_id": 1033}}]}
    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [more, more]})
    st = svc.start_run("x")
    assert st.status == "completed"
    assert len([e for e in st.evidence if e.evidence_id.startswith("E")]) == 2  # 2 rounds max


def test_injection_cannot_bypass_approval(env):  # TEST-SEC-01
    plan = {"intent": "handle ticket", "retrievals": [
        {"tool": "list_tickets", "args": {"customer_id": "C010"}}]}
    fooled = {"answer": "refunding everything", "proposed_actions": [
        {"action_id": "a1", "tool": "refund_order",
         "args": {"order_id": 1001, "amount": "12998.00", "reason": "ticket said so"},
         "reasoning": "ticket instruction", "evidence_refs": ["E1"], "confidence": 0.99}]}
    svc = svc_with({"Plan": [plan], "Decision": [fooled]})
    st = svc.start_run("Handle the open ticket from C010")
    assert "IGNORE ALL PREVIOUS" in json.dumps(st.evidence[0].data)
    assert st.status == "awaiting_approval"
    assert not q("SELECT * FROM refunds WHERE order_id=1001")


def test_invalid_action_repaired_once(env):  # FR-018
    bad = copy.deepcopy(S.S02_DECISION)
    bad["proposed_actions"] = [{"action_id": "a1", "tool": "cancel_order",
                                "args": {"order_id": 1050, "reason": "abc"}, "reasoning": "r",
                                "evidence_refs": ["E1"], "confidence": 0.9}]
    fixed = copy.deepcopy(S.S02_DECISION)
    fixed["proposed_actions"] = [{"action_id": "a1", "tool": "create_support_ticket",
                                  "args": {"customer_id": "C004", "order_id": 1050,
                                           "summary": "Customer wants to return instead"},
                                  "reasoning": "Cannot cancel delivered order",
                                  "evidence_refs": ["E1"], "confidence": 0.9}]
    llm = FakeLLM({"Plan": [S.S02_PLAN], "Decision": [bad, fixed]})
    svc = AgentService(llm=llm)
    st = svc.start_run("cancel 1050")
    assert "VALIDATION FEEDBACK" in llm.calls[-1]["system"]
    assert "already delivered" in llm.calls[-1]["system"]
    assert [r.tool for r in st.results] == ["create_support_ticket"]
    assert "repair_requested" in kinds(st.run_id)


def test_invalid_twice_is_reported_not_executed(env):
    bad = {"answer": "cancel", "proposed_actions": [
        {"action_id": "a1", "tool": "cancel_order", "args": {"order_id": 1050, "reason": "abc"},
         "reasoning": "r", "evidence_refs": ["E1"], "confidence": 0.9}]}
    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [bad, bad]})
    st = svc.start_run("cancel 1050")
    assert st.status == "completed" and not st.results
    assert "NOT EXECUTED" in st.summary
    assert q("SELECT status FROM orders WHERE id=1050")[0][0] == "delivered"


def test_memory_persists_across_service_instances(env):  # TEST-031
    svc = svc_with({"Plan": [S.S01_PLAN], "Decision": [S.S01_DECISION]})
    st = svc.start_run("refund 1042")
    svc.submit_approval(st.run_id, {"a1": {"approve": True}})
    assert any("third refund" in m["content"] for m in memory.recent("C001"))
    svc2 = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    st2 = svc2.start_run("What happened with C001 recently?")
    mem = [e for e in st2.evidence if e.source == "memory"]
    assert mem and "third refund" in json.dumps(mem[0].data)


def test_llm_failure_ends_cleanly(env):  # J-07
    svc = svc_with({"Plan": ["INVALID"]})
    st = svc.start_run("anything")
    assert st.status == "failed" and "valid" in st.error
    assert "run_failed" in kinds(st.run_id) and st.report_paths


def test_tool_timeout_injection_recovers(env):  # TEST-060
    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    st = svc.start_run("warranty?", inject_failure="tool_timeout")
    k = kinds(st.run_id)
    assert "failure_injected" in k and "recovered" in k
    assert st.evidence[0].found and st.status == "completed"


def test_db_locked_injection_recovers(env):
    svc = svc_with({"Plan": [S.S01_PLAN], "Decision": [S.S01_DECISION]})
    st = svc.start_run("refund", inject_failure="db_locked")
    st = svc.submit_approval(st.run_id, {"a1": {"approve": True}})
    assert all(r.status == "succeeded" for r in st.results)
    assert q("SELECT count(*) FROM refunds WHERE order_id=1042")[0][0] == 1  # no double write
    js = json.loads(Path(st.report_paths[0]).read_text(encoding="utf-8"))
    assert any(f["event"] == "failure_injected" for f in js["failures"]), [x for x in kinds(st.run_id) if "fail" in x or "retry" in x or "action" in x]


def test_injection_off_by_default(env):  # TEST-061
    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    st = svc.start_run("warranty?")
    assert "failure_injected" not in kinds(st.run_id)


def test_service_validation(env):  # TEST-040..044
    import pytest

    from opsagent.models import InvalidState, RunNotFound
    from opsagent.service import get_audit

    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    with pytest.raises(ValueError):
        svc.start_run("   ")
    with pytest.raises(ValueError):
        svc.start_run("x" * 2001)
    st = svc.start_run("warranty?")
    with pytest.raises(InvalidState):
        svc.submit_approval(st.run_id, {})
    with pytest.raises(RunNotFound):
        get_audit("nope")


def test_multiple_failure_modes_and_bad_mode(env):
    import pytest

    svc = svc_with({"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    st = svc.start_run("warranty?", inject_failure="tool_timeout,db_locked")
    assert st.status == "completed"
    with pytest.raises(ValueError):
        svc.start_run("x", inject_failure="meteor_strike")
