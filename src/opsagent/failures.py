"""Deliberate failure injection for demos and tests (FR-031, TASK-031).

A mode is armed per run (via OPSAGENT_INJECT_FAILURE or --inject-failure). Each armed mode
fires exactly once per run, so the agent's retry / repair path can be observed.
Off by default; prompts and tool args can never arm it.
"""

from __future__ import annotations

import threading

from .config import FAILURE_MODES

_lock = threading.Lock()
_armed: dict[str, set[str]] = {}
_fired: dict[str, set[str]] = {}


def parse_modes(value: str | None) -> list[str]:
    """Accepts one mode or a comma-separated list, e.g. 'tool_timeout,bad_llm_output'."""
    modes = [m.strip() for m in (value or "").split(",") if m.strip()]
    for m in modes:
        if m not in FAILURE_MODES:
            raise ValueError(f"unknown failure mode {m!r}; choose from {FAILURE_MODES}")
    return modes


def arm(run_id: str, mode: str | None) -> None:
    modes = parse_modes(mode)
    with _lock:
        for m in modes:
            _armed.setdefault(run_id, set()).add(m)


def armed(run_id: str) -> set[str]:
    with _lock:
        return set(_armed.get(run_id, set()))


def should_fail(run_id: str | None, mode: str) -> bool:
    """True exactly once per (run, mode) when that mode is armed."""
    if not run_id:
        return False
    with _lock:
        if mode not in _armed.get(run_id, set()):
            return False
        fired = _fired.setdefault(run_id, set())
        if mode in fired:
            return False
        fired.add(mode)
        return True


def clear(run_id: str) -> None:
    with _lock:
        _armed.pop(run_id, None)
        _fired.pop(run_id, None)
