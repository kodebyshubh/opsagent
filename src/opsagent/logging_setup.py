"""Application logging: console (warnings) + rotating file (all)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    from .config import PROJECT_ROOT

    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    root = logging.getLogger("opsagent")
    root.setLevel(level)
    fh = RotatingFileHandler(logs / "opsagent.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("  [opsagent] %(message)s"))
    root.addHandler(ch)
    _CONFIGURED = True
