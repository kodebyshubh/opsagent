import json

import pytest

from opsagent import audit, failures, llm
from opsagent.llm import LLMClient, extract_json
from opsagent.models import LLMUnavailableError, Plan, StructuredOutputError
from tests.fakes import StubChatModel

PLAN = json.dumps({"intent": "x", "retrievals": [], "anticipated_steps": []})


def _client(env, outputs):
    stub = StubChatModel(outputs)
    return LLMClient(env, model_factory=lambda s: stub), stub


def test_extract_json_handles_fences_and_decimals():
    d = extract_json('```json\n{"amount": 1299.99}\n```')
    assert str(d["amount"]) == "1299.99"


def test_repair_after_invalid_output(env):  # TEST-011
    c, stub = _client(env, ['{"intent": 5', PLAN])
    rid = audit.new_run("q")
    assert isinstance(c(Plan, "sys", "user", run_id=rid, node="plan"), Plan)
    assert stub.calls == 2
    kinds = [e["event_type"] for e in audit.get_events(rid)]
    assert "structured_output_invalid" in kinds and "recovered" in kinds


def test_two_invalid_outputs_raise(env):
    c, _ = _client(env, ["nope", "still nope"])
    with pytest.raises(StructuredOutputError):
        c(Plan, "sys", "user", run_id=None, node="plan")


def test_transport_retry_then_fail(env, monkeypatch):
    monkeypatch.setattr(llm, "TRANSPORT_BACKOFF", (0.0, 0.0, 0.0))
    c, stub = _client(env, [RuntimeError("boom")] * 4)
    with pytest.raises(LLMUnavailableError):
        c(Plan, "sys", "user", run_id=None, node="plan")
    assert stub.calls == 4


def test_transport_retry_recovers(env, monkeypatch):
    monkeypatch.setattr(llm, "TRANSPORT_BACKOFF", (0.0, 0.0, 0.0))
    c, _ = _client(env, [RuntimeError("429 try again in 0.01s"), PLAN])
    assert c(Plan, "sys", "u", run_id=None, node="plan").intent == "x"


def test_injected_bad_output_is_repaired(env):  # TEST-060 (bad_llm_output)
    decision = json.dumps({"answer": "ok"})
    c, stub = _client(env, [decision, decision])
    rid = audit.new_run("q")
    failures.arm(rid, "bad_llm_output")
    from opsagent.models import Decision

    assert c(Decision, "sys", "u", run_id=rid, node="reason").answer == "ok"
    assert stub.calls == 2
    kinds = [e["event_type"] for e in audit.get_events(rid)]
    assert kinds.count("failure_injected") == 1 and "recovered" in kinds


def test_retry_delay_parsing():
    assert llm._retry_delay(Exception("Please try again in 7.5s."), 4) == 8.5
    assert llm._retry_delay(Exception("try again in 1m2s"), 4) == 60.0
    assert llm._retry_delay(Exception("other"), 4) == 4


def test_throttle_waits_when_budget_used(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))
    clock = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    t = llm.TokenThrottle(8000)
    assert t.wait(5000) == 0.0
    t.record(5000)
    clock[0] += 10

    def advance(s):  # simulated sleep moves the clock forward
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(llm.time, "sleep", advance)
    waited = t.wait(4000)  # 5000 + 4000 > 8000 -> must wait until the first call ages out
    assert waited > 49 and sleeps


def test_rate_limit_is_retried_not_failed(env, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    err = RuntimeError("Error code: 429 rate_limit_exceeded. Please try again in 12.5s.")
    c, stub = _client(env, [err, err, err, err, PLAN])
    assert c(Plan, "sys", "u", run_id=None, node="plan").intent == "x"
    assert stub.calls == 5


def test_provider_json_validation_error_is_retried(env, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    err = RuntimeError("Error code: 400 - {'code': 'json_validate_failed'}")
    c, stub = _client(env, [err, PLAN])
    assert c(Plan, "sys", "u", run_id=None, node="plan").intent == "x"
    assert stub.calls == 2
