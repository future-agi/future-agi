"""
Scoring utility functions for the evals revamp (Phase 2).

Provides normalized 0-1 scoring, pass/fail determination,
and choice-to-score mapping.

These utilities are used by the revamped eval system.
Existing eval execution code in _run_eval / calculate_eval_average
is NOT modified — these coexist.
"""

from __future__ import annotations


def normalize_score(
    value,
    output_type: str = "percentage",
    choice_scores: dict[str, float] | None = None,
) -> float:
    """
    Normalize any eval output to a 0-1 float.

    Args:
        value: The raw eval output (str, float, int, bool, list)
        output_type: "pass_fail", "percentage", or "deterministic"
        choice_scores: Dict mapping choice labels to 0-1 scores

    Returns:
        Normalized score as float in [0.0, 1.0]
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

    if output_type == "percentage":
        try:
            score = float(value)
            return max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            return 0.0

    if output_type == "deterministic":
        if choice_scores and isinstance(value, str):
            return apply_choice_scores(value, choice_scores) or 0.0
        if choice_scores and isinstance(value, list) and len(value) > 0:
            # For single-choice: use the first element
            return apply_choice_scores(str(value[0]), choice_scores) or 0.0
        try:
            return max(0.0, min(1.0, float(value)))
        except (ValueError, TypeError):
            return 0.0

    # Fallback
    try:
        return max(0.0, min(1.0, float(value)))
    except (ValueError, TypeError):
        return 0.0


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


def aggregate_choice_scores(
    labels, choice_scores: dict[str, float]
) -> float | None:
    """
    Average the scores of a list of selected labels (multi-choice).

    Used by ``format_eval_value`` when a multi-choice eval has
    ``choice_scores`` declared. Returns None if no labels resolve to a
    score in the mapping.
    """
    if not labels or not choice_scores:
        return None
    resolved: list[float] = []
    for label in labels:
        score = apply_choice_scores(str(label), choice_scores)
        if score is not None:
            resolved.append(float(score))
    if not resolved:
        return None
    return sum(resolved) / len(resolved)


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
