import json
from pathlib import Path

from opsagent.knowledge import registry
from opsagent.knowledge import sql_tools as st
from opsagent.knowledge.policy_store import search
from opsagent.models import RetrievalRequest


def test_seed_files_have_demo_anchors():  # TEST-002
    orders = {o["id"]: o for o in json.loads(Path("data/seed/orders.json").read_text(encoding="utf-8"))}
    for oid in (1033, 1042, 1044, 1050, 1051, 1063, 1077):
        assert oid in orders
    assert orders[1042]["total_amount"] == "6499.00"
    assert orders[1033]["total_amount"] == "450.50"
    open_priya = [o for o in orders.values() if o["customer_id"] == "C002"
                  and o["status"] in ("placed", "packed")]
    assert len(open_priya) == 3
    catalog = {p["sku"]: p for p in json.loads(Path("data/catalog.json").read_text(encoding="utf-8"))}
    assert catalog["SKU-EB-007"]["returnable"] is False


def test_seed_counts(env):  # TEST-003, TEST-004
    from opsagent.seed import seed_all

    counts = seed_all(include_policies=False)
    assert counts["customers"] == 16 and counts["orders"] == 36
    assert counts == seed_all(include_policies=False)


def test_policy_search_has_metadata(env):  # TEST-005
    hits = search("refund return window days", k=6)
    sources = {h["source"] for h in hits}
    assert "refund_policy_v2.md" in sources
    assert all("effective_date" in h for h in hits)


def test_get_order_money_and_days(env):  # TEST-007
    o = st.get_order(st.OrderArgs(order_id=1042))
    assert o["total_amount"] == "6499.00"
    assert o["days_since_delivery"] == 20
    assert o["refundable_remaining"] == "6499.00"


def test_not_found_does_not_raise(env):  # TEST-008
    assert st.get_order(st.OrderArgs(order_id=9999)) is None
    ev = registry.run_parallel([RetrievalRequest(tool="get_order", args={"order_id": 9999})], None)
    assert ev[0].found is False and ev[0].error == "Not found"


def test_unknown_tool_and_bad_args(env):  # TEST-009
    ev = registry.run_parallel([
        RetrievalRequest(tool="drop_tables", args={}),
        RetrievalRequest(tool="get_order", args={"order_id": "abc"}),
    ], None)
    assert ev[0].found is False and "Unknown" in ev[0].error
    assert ev[1].found is False and "Invalid args" in ev[1].error


def test_retrieval_runs_in_parallel(env, monkeypatch):  # TEST-010
    import time

    spec = registry.RETRIEVAL_TOOLS["get_order"]
    slow = registry.ToolSpec(spec.name, spec.source, spec.description, spec.args_model,
                             lambda a: (time.sleep(0.3), spec.fn(a))[1])
    monkeypatch.setitem(registry.RETRIEVAL_TOOLS, "get_order", slow)
    t0 = time.perf_counter()
    ev = registry.run_parallel(
        [RetrievalRequest(tool="get_order", args={"order_id": i}) for i in (1042, 1033, 1050)],
        None)
    assert time.perf_counter() - t0 < 0.75
    assert [e.evidence_id for e in ev] == ["E1", "E2", "E3"]


def test_placeholder_args_are_not_executed(env):
    ev = registry.run_parallel([
        RetrievalRequest(tool="get_customer_memory", args={"customer_id": "<from find_customer>"}),
        RetrievalRequest(tool="get_product", args={"sku": "{{sku}}"}),
    ], None)
    assert all(not e.found and "placeholder" in e.error for e in ev)


def test_policy_search_surfaces_superseded_version(env):
    hits = search("refund eligibility return window policy", k=5)
    legacy = [h for h in hits if h["source"] == "returns_faq_legacy.md"]
    assert legacy and legacy[0]["superseded_by"].startswith("refund_policy_v2.md")
    assert "15 days" in legacy[0]["text"]


def test_version_notice_in_rendered_evidence(env):
    from opsagent.graph.nodes import render_evidence

    ev = registry.run_parallel([RetrievalRequest(
        tool="search_policies", args={"query": "refund eligibility return window policy"})], None)
    text = render_evidence([e.model_dump(mode="json") for e in ev])
    assert "VERSION NOTICE" in text and "returns_faq_legacy.md" in text
