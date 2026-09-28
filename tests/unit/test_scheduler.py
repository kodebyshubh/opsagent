import json

from opsagent import audit, scheduler
from opsagent.scheduler import ExecItem, plan_groups


def _item(aid, entity, deps=(), tool="restock_item", args=None):
    return ExecItem(aid, tool, args or {"sku": entity.split(":")[1], "quantity": 1,
                                         "reason": "abc"}, entity, list(deps))


def test_levels():  # TEST-024
    g = plan_groups([_item("a1", "order:1"), _item("a2", "sku:A", ["a1"]),
                     _item("a3", "sku:B", ["a1"])])
    assert [[i.action_id for i in grp] for grp in g] == [["a1"], ["a2", "a3"]]


def test_same_entity_serialized():  # TEST-025
    g = plan_groups([_item("a1", "sku:A"), _item("a2", "sku:A"), _item("a3", "sku:B")])
    assert [[i.action_id for i in grp] for grp in g] == [["a1", "a3"], ["a2"]]


def _run_id(env):
    return audit.new_run("scheduler test")


def test_skip_propagation(env):  # TEST-026
    rid = _run_id(env)
    items = [_item("a2", "sku:SKU-HP-001", ["a1"], args={"sku": "SKU-HP-001", "quantity": 1,
                                                         "reason": "abc"}),
             _item("a3", "sku:SKU-BT-008", [], args={"sku": "SKU-BT-008", "quantity": 1,
                                                    "reason": "abc"})]
    for i in items:
        scheduler.upsert_record(rid, i.action_id, i.tool, i.args, "LOW", "approved")
    res = {r.action_id: r for r in scheduler.execute(items, rid, blocked={"a1": "rejected"})}
    assert res["a2"].status == "skipped" and "a1" in res["a2"].error
    assert res["a3"].status == "succeeded"


def test_failure_skips_dependents(env):
    rid = _run_id(env)
    items = [ExecItem("a1", "cancel_order", {"order_id": 1050, "reason": "abc"}, "order:1050"),
             _item("a2", "sku:SKU-HP-001", ["a1"], args={"sku": "SKU-HP-001", "quantity": 1,
                                                         "reason": "abc"})]
    for i in items:
        scheduler.upsert_record(rid, i.action_id, i.tool, i.args, "LOW", "approved")
    res = {r.action_id: r for r in scheduler.execute(items, rid)}
    assert res["a1"].status == "failed" and res["a2"].status == "skipped"


def test_idempotent(env):  # TEST-027
    rid = _run_id(env)
    it = _item("a1", "sku:SKU-TS-010", args={"sku": "SKU-TS-010", "quantity": 5, "reason": "abc"})
    scheduler.upsert_record(rid, "a1", it.tool, it.args, "LOW", "approved")
    first = scheduler.execute([it], rid)[0]
    second = scheduler.execute([it], rid)[0]
    assert first.result["new_stock"] == 5 and second.result == first.result
    from opsagent.db import connect
    c = connect()
    assert c.execute("SELECT stock FROM inventory WHERE sku='SKU-TS-010'").fetchone()[0] == 5
    c.close()


def test_parallel_group_overlaps(env):
    rid = _run_id(env)
    items = [_item("a1", "sku:SKU-HP-001", args={"sku": "SKU-HP-001", "quantity": 1, "reason": "abc"}),
             _item("a2", "sku:SKU-BT-008", args={"sku": "SKU-BT-008", "quantity": 1, "reason": "abc"})]
    for i in items:
        scheduler.upsert_record(rid, i.action_id, i.tool, i.args, "LOW", "approved")
    res = scheduler.execute(items, rid)
    assert {r.group for r in res} == {1}
    ev = [e for e in audit.get_events(rid) if e["event_type"] == "execution_plan"]
    assert json.dumps(ev[0]["payload"]).count("restock_item") == 2
