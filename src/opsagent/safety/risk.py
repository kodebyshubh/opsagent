"""Risk classification and approval requirement (TASK-019, DEC-004, DEC-015)."""

from __future__ import annotations

from typing import Any

from ..actions.registry import ActionSpec
from ..config import Settings


def classify(
    spec: ActionSpec, args: Any, confidence: float, settings: Settings
) -> tuple[str, bool, str]:
    """Return (risk_tier, requires_approval, reason)."""
    tier, reason = spec.risk(args, settings)
    requires = tier == "HIGH"
    if confidence < settings.min_confidence:
        requires = True
        reason = (
            f"{reason} Agent confidence {confidence:.2f} is below "
            f"{settings.min_confidence:.2f}, so a human must approve."
        ).strip()
    return tier, requires, reason
