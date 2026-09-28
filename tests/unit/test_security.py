import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "opsagent"


def _code():
    return {p: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}


def test_no_string_built_sql():  # TEST-SEC-03
    bad = re.compile(r"execute\(\s*f[\"']|execute\([^)]*\.format\(|execute\([^)]*%\s*\(")
    offenders = [str(p) for p, s in _code().items() if bad.search(s)]
    # seed.py counts rows over a fixed tuple of table names (not user input)
    assert [o for o in offenders if not o.endswith("seed.py")] == []


def test_audit_is_append_only():  # TEST-015
    for p, s in _code().items():
        assert not re.search(r"(UPDATE|DELETE FROM)\s+audit_events", s), p


def test_env_is_gitignored():  # TEST-SEC-04
    gi = (SRC.parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi.splitlines()
