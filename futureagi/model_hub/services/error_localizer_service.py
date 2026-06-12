"""Domain logic for whether an error-localization task should run."""

from __future__ import annotations

from typing import Any

from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.scoring import determine_pass_fail, normalize_score


def _extract_eval_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("failure"), bool):
        return not value["failure"]
    for key in ("score", "result", "output", "choice"):
        if value.get(key) is not None:
            return value[key]
    return value


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
    score = normalize_score(_extract_eval_value(value), output_type, choice_scores)

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
