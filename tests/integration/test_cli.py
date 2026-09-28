from typer.testing import CliRunner

from opsagent import cli
from opsagent.service import AgentService
from tests import scripts as S
from tests.fakes import FakeLLM

runner = CliRunner()


def _patch(monkeypatch, script):
    monkeypatch.setattr(cli, "_service", lambda: AgentService(llm=FakeLLM(script)))


def test_help():
    r = runner.invoke(cli.app, ["--help"])
    assert r.exit_code == 0 and "ask" in r.output and "audit" in r.output


def test_seed_command(env):
    r = runner.invoke(cli.app, ["seed", "--no-policies"])
    assert r.exit_code == 0 and "orders" in r.output


def test_ask_with_interactive_approval(env, monkeypatch, tmp_path):  # TEST-019, TEST-029
    _patch(monkeypatch, {"Plan": [S.S01_PLAN], "Decision": [S.S01_DECISION]})
    t = tmp_path / "t.txt"
    r = runner.invoke(cli.app, ["ask", "refund 1042 and restock", "--transcript", str(t)],
                      input="y\n")
    assert r.exit_code == 0, r.output
    text = t.read_text(encoding="utf-8")
    for s in ("PLAN", "EVIDENCE", "CONFLICT DETECTED", "APPROVAL REQUIRED", "EXECUTION",
              "SUMMARY"):
        assert s in text
    assert "succeeded" in text


def test_ask_clarification_with_answer(env, monkeypatch):
    _patch(monkeypatch, {"Plan": [S.S05_PLAN, S.S05_PLAN2],
                         "Decision": [S.S05_CLARIFY, S.S05_DECISION]})
    r = runner.invoke(cli.app, ["ask", "Cancel Priya's order", "--answer", "1063",
                                "--approve", "none"])
    assert r.exit_code == 0, r.output
    assert "CLARIFICATION NEEDED" in r.output and "rejected" in r.output


def test_runs_audit_memory(env, monkeypatch):  # TEST-032, TEST-033
    _patch(monkeypatch, {"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    runner.invoke(cli.app, ["ask", "warranty?"])
    from opsagent.audit import list_runs

    rid = list_runs(1)[0]["run_id"]
    assert rid in runner.invoke(cli.app, ["runs"]).output
    out = runner.invoke(cli.app, ["audit", "--run", rid]).output
    assert "plan_created" in out and "run_completed" in out
    assert "Refunded" in runner.invoke(cli.app, ["memory", "--customer", "C001"]).output


def test_missing_db_message(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAGENT_DB_PATH", str(tmp_path / "none.db"))
    from opsagent.config import reset_settings

    reset_settings()
    r = runner.invoke(cli.app, ["ask", "hi"])
    reset_settings()
    assert r.exit_code == 1 and "opsagent seed" in r.output


def test_recovery_panel_shown_for_injected_failure(env, monkeypatch):
    _patch(monkeypatch, {"Plan": [S.S02_PLAN], "Decision": [S.S02_DECISION]})
    r = runner.invoke(cli.app, ["ask", "warranty?", "--inject-failure", "tool_timeout"])
    assert r.exit_code == 0, r.output
    assert "FAILURES AND RECOVERY" in r.output and "recovered" in r.output
