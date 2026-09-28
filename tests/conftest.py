from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated, freshly seeded store + local embeddings (no network)."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("OPSAGENT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("OPSAGENT_CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("OPSAGENT_CHECKPOINT_PATH", str(tmp_path / "cp.db"))
    monkeypatch.setenv("OPSAGENT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("OPSAGENT_EMBEDDINGS", "local")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used")
    monkeypatch.setenv("OPSAGENT_INJECT_FAILURE", "")
    monkeypatch.setenv("OPSAGENT_USE_REAL_DATE", "0")
    monkeypatch.setenv("OPSAGENT_REFUND_APPROVAL_THRESHOLD", "5000.00")
    monkeypatch.setenv("OPSAGENT_MIN_CONFIDENCE", "0.6")
    monkeypatch.setenv("OPSAGENT_TPM_LIMIT", "0")  # no client-side pacing in tests
    from opsagent.config import get_settings, reset_settings

    reset_settings()
    from opsagent.seed import seed_all

    seed_all()
    yield get_settings()
    reset_settings()
    os.environ.pop("OPSAGENT_DB_PATH", None)
