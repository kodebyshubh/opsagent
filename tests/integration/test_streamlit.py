from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[2] / "ui" / "streamlit_app.py")


def test_streamlit_renders(env, monkeypatch):  # TEST-050
    st_testing = pytest.importorskip("streamlit.testing.v1")
    import opsagent.service as service_mod
    from tests import scripts as S
    from tests.fakes import FakeLLM

    real = service_mod.AgentService
    monkeypatch.setattr(service_mod, "AgentService",
                        lambda: real(llm=FakeLLM({"Plan": [S.S01_PLAN],
                                                  "Decision": [S.S01_DECISION]})))
    at = st_testing.AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert any("OpsAgent" in t.value for t in at.title)
    at.text_area(key="query").set_value("refund 1042").run()
    run_btn = [b for b in at.button if b.label == "Run agent"][0]
    run_btn.click().run()
    assert not at.exception
    assert any("Human approval required" in m.value for m in at.markdown)
    [b for b in at.button if b.label == "Submit decisions"][0].click().run()
    assert not at.exception
    assert any("Actions" in s.value for s in at.success)
