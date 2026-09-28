"""Central settings loaded from environment variables and .env (TASK-002)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Fixed demo date so refund windows and "days since delivery" are reproducible (DEC-014).
SEED_TODAY = date(2026, 9, 27)

FAILURE_MODES = ("tool_timeout", "bad_llm_output", "db_locked")

# Repo root (src/opsagent/config.py -> repo). Relative paths are resolved against it so the
# commands work from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def _abs(p: str | None) -> Path:
    path = Path(p or "")
    return path if path.is_absolute() else PROJECT_ROOT / path


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _mask(value: str | None) -> str:
    if not value:
        return "<unset>"
    return value[:4] + "****"


@dataclass(frozen=True)
class Settings:
    provider: str = "groq"
    model: str = "openai/gpt-oss-120b"
    groq_api_key: str | None = field(default=None, repr=False)
    google_api_key: str | None = field(default=None, repr=False)
    db_path: Path = Path("data/opsagent.db")
    chroma_dir: Path = Path("data/chroma")
    checkpoint_path: Path = Path("data/checkpoints.db")
    reports_dir: Path = Path("reports")
    refund_approval_threshold: Decimal = Decimal("5000.00")
    min_confidence: float = 0.6
    max_actions: int = 5
    max_retrieval_rounds: int = 2
    llm_timeout: float = 60.0
    tpm_limit: int = 8000  # provider tokens-per-minute budget (0 disables client pacing)
    reasoning_effort: str | None = "low"
    log_level: str = "INFO"
    use_real_date: bool = False
    inject_failure: str | None = None

    def __repr__(self) -> str:  # never print keys
        return (
            f"Settings(provider={self.provider!r}, model={self.model!r}, "
            f"groq_api_key={_mask(self.groq_api_key)!r}, "
            f"google_api_key={_mask(self.google_api_key)!r}, db_path={str(self.db_path)!r}, "
            f"threshold={self.refund_approval_threshold}, min_confidence={self.min_confidence})"
        )

    __str__ = __repr__

    def today(self) -> date:
        return date.today() if self.use_real_date else SEED_TODAY

    def require_llm(self) -> None:
        """Fail fast with a helpful message if the provider key is missing."""
        if self.provider == "groq" and not self.groq_api_key:
            raise ConfigError(
                "Set GROQ_API_KEY in .env (see README setup step 3), "
                "or set OPSAGENT_PROVIDER=gemini with GOOGLE_API_KEY."
            )
        if self.provider == "gemini" and not self.google_api_key:
            raise ConfigError("Set GOOGLE_API_KEY in .env or switch OPSAGENT_PROVIDER to groq.")
        if self.provider not in ("groq", "gemini"):
            raise ConfigError(f"Unknown OPSAGENT_PROVIDER {self.provider!r} (use groq or gemini).")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _decimal(name: str, default: str) -> Decimal:
    raw = _env(name, default)
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be a number like 5000.00, got {raw!r}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    failure = _env("OPSAGENT_INJECT_FAILURE")
    if failure and any(m.strip() not in FAILURE_MODES for m in failure.split(",")):
        raise ConfigError(f"OPSAGENT_INJECT_FAILURE must be one or more of {FAILURE_MODES}")
    return Settings(
        provider=(_env("OPSAGENT_PROVIDER", "groq") or "groq").lower(),
        model=_env("OPSAGENT_MODEL", "openai/gpt-oss-120b") or "openai/gpt-oss-120b",
        groq_api_key=_env("GROQ_API_KEY"),
        google_api_key=_env("GOOGLE_API_KEY"),
        db_path=_abs(_env("OPSAGENT_DB_PATH", "data/opsagent.db")),
        chroma_dir=_abs(_env("OPSAGENT_CHROMA_DIR", "data/chroma")),
        checkpoint_path=_abs(_env("OPSAGENT_CHECKPOINT_PATH", "data/checkpoints.db")),
        reports_dir=_abs(_env("OPSAGENT_REPORTS_DIR", "reports")),
        refund_approval_threshold=_decimal("OPSAGENT_REFUND_APPROVAL_THRESHOLD", "5000.00"),
        min_confidence=float(_env("OPSAGENT_MIN_CONFIDENCE", "0.6")),
        tpm_limit=int(_env("OPSAGENT_TPM_LIMIT", "8000") or 0),
        reasoning_effort=_env("OPSAGENT_REASONING_EFFORT", "low"),
        log_level=(_env("OPSAGENT_LOG_LEVEL", "INFO") or "INFO").upper(),
        use_real_date=_env("OPSAGENT_USE_REAL_DATE", "0") == "1",
        inject_failure=failure,
    )


def reset_settings() -> None:
    """Clear the cached settings (used by tests)."""
    get_settings.cache_clear()
