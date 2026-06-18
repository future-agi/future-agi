"""Domain logic for whether an error-localization task should run."""

from __future__ import annotations

from typing import Any

from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.scoring import determine_pass_fail, normalize_score


def error_localizer_enabled(eval_config: Any) -> bool:
    if eval_config is None:
        return False
    if bool(getattr(eval_config, "error_localizer", False)):
        return True
    config = getattr(eval_config, "config", None) or {}
    return bool(config.get("error_localizer_enabled"))


def _extract_eval_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("failure"), bool):
        return not value["failure"]
    for key in ("score", "result", "output", "choice"):
        if value.get(key) is not None:
            return value[key]
    return value


_PASS_FAIL_KEYWORDS = frozenset(
    {"passed", "pass", "true", "yes", "failed", "fail", "false", "no"}
)


def _is_numerically_scorable(
    value: Any, output_type: str, choice_scores: dict[str, float]
) -> bool:
    if value is None:
        return False
    if output_type == "pass_fail":
        if isinstance(value, (bool, int, float)):
            return True
        if isinstance(value, str):
            return value.strip().lower() in _PASS_FAIL_KEYWORDS
        return False
    if output_type == "deterministic":
        if not choice_scores:
            return False
        if isinstance(value, str):
            return value in choice_scores
        if isinstance(value, list) and value:
            return all(isinstance(v, str) and v in choice_scores for v in value)
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


def should_run_error_localizer(
    value: Any, eval_template: EvalTemplate | None
) -> tuple[bool, str]:
    if eval_template is None:
        return (
            False,
            "Error localization skipped — no eval template is attached to this evaluation.",
        )
    if getattr(eval_template, "eval_type", "") == "code":
        return False, "Error localization skipped — not applicable to code-type evals."
    if getattr(eval_template, "template_type", "single") == "composite":
        return (
            False,
            "Error localization skipped — composite evals are not yet supported.",
        )

    output_type = getattr(eval_template, "output_type_normalized", None) or "percentage"
    choice_scores = getattr(eval_template, "choice_scores", None) or {}
    extracted = _extract_eval_value(value)

    if not _is_numerically_scorable(extracted, output_type, choice_scores):
        return (
            False,
            "Error localization skipped — this evaluation result is not eligible for localization.",
        )

    score = normalize_score(extracted, output_type, choice_scores)

    threshold = getattr(eval_template, "pass_threshold", None)
    threshold = float(threshold) if threshold is not None else 0.5

    decision = determine_pass_fail(score, threshold)
    if output_type == "pass_fail":
        if decision:
            return False, "Error localization skipped — the evaluation passed."
        return True, "The evaluation failed."
    if decision:
        return False, (
            f"Error localization skipped — the evaluation passed "
            f"(score {score:.2f}, threshold {threshold:.2f})."
        )
    return True, (
        f"The evaluation failed (score {score:.2f}, threshold {threshold:.2f})."
    )
