"""Helpers for LLM-judge score handling.

Used by CustomPromptEvaluator and AgentEvaluator only. Function /
deterministic / code / similarity evaluators compute their own scores
deterministically and must NEVER call into this module.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def clamp_unit_score(raw: Any) -> Any:
    """Clamp an LLM-judge score into [0, 1].

    None is preserved. Non-numeric values are returned unchanged so the
    caller decides how to handle them. Booleans are coerced via float()
    (True -> 1.0, False -> 0.0). A warning is logged on every clamp.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return raw
    if v < 0.0 or v > 1.0:
        logger.warning(
            "eval_score_out_of_range_clamped",
            raw_value=v,
            clamped_to=max(0.0, min(1.0, v)),
        )
    return max(0.0, min(1.0, v))


__all__ = ["clamp_unit_score"]
