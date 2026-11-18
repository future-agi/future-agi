"""
Scoring utility functions for the evals revamp (Phase 2).

Provides normalized 0-1 scoring, pass/fail determination,
and choice-to-score mapping.

These utilities are used by the revamped eval system.
Existing eval execution code in _run_eval / calculate_eval_average
is NOT modified — these coexist.
"""

from __future__ import annotations

import math
from typing import Any


def _to_clamped_score(value) -> float:
    if value is None:
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(score):
        return 0.0
    return max(0.0, min(1.0, score))


def extract_eval_value(value: Any) -> Any:
    """Unwrap a dict eval output to its scalar via failure/score/result/output/choice/value."""
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("failure"), bool):
        return not value["failure"]
    for key in ("score", "result", "output", "choice", "value"):
        if value.get(key) is not None:
            return value[key]
    return value


_PASS_FAIL_TOKENS = frozenset(
    {"passed", "pass", "true", "yes", "failed", "fail", "false", "no"}
)


def is_numerically_scorable(
    value: Any, output_type: str, choice_scores: dict[str, float]
) -> bool:
    """True when ``normalize_score`` can produce a meaningful score for ``value``."""
    if value is None:
        return False
    if output_type == "pass_fail":
        if isinstance(value, (bool, int, float)):
            return True
        if isinstance(value, str):
            return value.strip().lower() in _PASS_FAIL_TOKENS
        return False
    if output_type == "deterministic":
        if not choice_scores:
            return False
        if isinstance(value, str):
            return apply_choice_scores(value, choice_scores) is not None
        if isinstance(value, list) and value:
            return any(
                isinstance(v, str) and apply_choice_scores(v, choice_scores) is not None
                for v in value
            )
        return isinstance(value, (int, float))
    if isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    return False


def normalize_score(
    value,
    output_type: str = "percentage",
    choice_scores: dict[str, float] | None = None,
) -> float:
    """
    Normalize any eval output to a finite float in [0.0, 1.0].

    Args:
        value: The raw eval output (str, float, int, bool, list)
        output_type: "pass_fail", "percentage", or "deterministic"
        choice_scores: Dict mapping choice labels to 0-1 scores

    Returns:
        Normalized score as float in [0.0, 1.0]; never NaN; never raises.
    """
    if value is None:
        return 0.0

    if output_type == "pass_fail":
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, str):
            return 1.0 if value.lower() in ("passed", "pass", "true", "yes") else 0.0
        if isinstance(value, (int, float)):
            return 1.0 if value > 0 else 0.0
        return 0.0

    if output_type == "deterministic":
        if choice_scores and isinstance(value, str):
            return apply_choice_scores(value, choice_scores) or 0.0
        if choice_scores and isinstance(value, list) and value:
            mapped = [apply_choice_scores(str(v), choice_scores) for v in value]
            mapped = [m for m in mapped if m is not None]
            return sum(mapped) / len(mapped) if mapped else 0.0
        return _to_clamped_score(value)

    return _to_clamped_score(value)


def determine_pass_fail(score: float, threshold: float = 0.5) -> bool:
    """
    Determine if a score passes based on the threshold.

    Args:
        score: Normalized score (0-1)
        threshold: Pass threshold (0-1). Scores >= threshold pass.

    Returns:
        True if score >= threshold, False otherwise
    """
    return score >= threshold


def apply_choice_scores(
    choice_label: str, choice_scores: dict[str, float]
) -> float | None:
    """
    Map a choice label to its numeric score (case-insensitive).

    Args:
        choice_label: The choice label (e.g., "Yes", "No", "Maybe")
        choice_scores: Dict mapping labels to 0-1 scores

    Returns:
        The numeric score, or None if the label is not in the mapping
    """
    if not choice_scores or not choice_label:
        return None
    # Exact match first
    if choice_label in choice_scores:
        return choice_scores[choice_label]
    # Case-insensitive fallback
    label_lower = choice_label.strip().lower()
    for key, score in choice_scores.items():
        if key.strip().lower() == label_lower:
            return score
    return None


def validate_choice_scores(choice_scores: dict) -> list[str]:
    """
    Validate a choice_scores dict.

    Rules:
    - Must be a non-empty dict
    - All keys must be non-empty strings
    - All values must be floats/ints in [0.0, 1.0]

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not isinstance(choice_scores, dict):
        return ["choice_scores must be a dictionary"]

    if not choice_scores:
        return ["choice_scores must not be empty"]

    for key, value in choice_scores.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(f"Choice key must be a non-empty string, got: {key!r}")
            continue

        if not isinstance(value, (int, float)):
            errors.append(
                f"Choice '{key}' score must be a number, got: {type(value).__name__}"
            )
            continue

        if value < 0.0 or value > 1.0:
            errors.append(f"Choice '{key}' score must be between 0 and 1, got: {value}")

    return errors


def validate_pass_threshold(threshold) -> list[str]:
    """
    Validate a pass_threshold value.

    Rules:
    - Must be a float/int
    - Must be in [0.0, 1.0]

    Returns:
        List of error messages (empty if valid)
    """
    if not isinstance(threshold, (int, float)):
        return [f"pass_threshold must be a number, got: {type(threshold).__name__}"]

    if threshold < 0.0 or threshold > 1.0:
        return [f"pass_threshold must be between 0 and 1, got: {threshold}"]

    return []


def score_eval_output(
    value_or_result: Any,
    eval_template: Any,
    default_score: float | None = 0.0,
) -> float | None:
    """Canonical eval-output → normalized 0-1 score. Falls back to
    ``default_score`` when the value cannot be interpreted under the
    template's output type; pass ``default_score=None`` to surface that
    case as ``None`` instead of a hard 0.0."""
    if hasattr(value_or_result, "eval_results"):
        from evaluations.engine.formatting import (
            extract_raw_result,
            format_eval_value,
        )

        raw = extract_raw_result(value_or_result, eval_template)
        value_or_result = format_eval_value(raw, eval_template)

    scalar = extract_eval_value(value_or_result)
    output_type = getattr(eval_template, "output_type_normalized", None) or "percentage"
    choice_scores = getattr(eval_template, "choice_scores", None) or {}

    if not is_numerically_scorable(scalar, output_type, choice_scores):
        return default_score

    return normalize_score(scalar, output_type, choice_scores)
