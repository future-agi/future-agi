"""Builder for the standard ``EvalResult`` envelope."""

from __future__ import annotations

from typing import Any

from agentic_eval.core_evals.fi_utils.evals_result import EvalResult


def build_eval_result(
    *,
    name: str,
    display_name: str,
    result_value: Any,
    failure: bool,
    explanation: str,
    runtime_ms: int,
    model: str | None,
    metric_id: str,
    metadata: str,
    datapoint_field_annotations: Any = None,
) -> EvalResult:
    """Build the standard ``EvalResult`` dict produced by every evaluator."""
    return {
        "name": name,
        "display_name": display_name,
        "data": {"result": result_value},
        "failure": failure,
        "metadata": metadata,
        "reason": explanation,
        "runtime": runtime_ms,
        "model": model,
        "metrics": [{"id": metric_id, "value": result_value}],
        "datapoint_field_annotations": datapoint_field_annotations,
    }


def compute_eval_failure(
    *,
    output_type: str,
    result_value: Any,
    pass_threshold: float = 0.5,
    reverse_output: bool = False,
    choices: list[str] | None = None,
    choice_scores: dict[str, float] | None = None,
    multi_choice: bool = False,
) -> bool:
    """Derive the failure bit from a judge's parsed output.

    Single source of truth for failure derivation across every LLM-as-judge
    evaluator. Behaviour matrix:

    - ``Pass/Fail``: failure when ``result_value`` lower-cases to one of
      ``fail/failed/false/0``.
    - ``score`` / ``numeric``: failure when ``float(result_value) < pass_threshold``.
      Unparseable values fail safe.
    - ``choices`` with ``choice_scores``: lookup the label(s) case-insensitively,
      average for multi-choice, threshold against ``pass_threshold``. Unknown
      labels are treated as a missing score and fail safe.
    - ``choices`` without ``choice_scores``: ordinal convention — the first
      declared choice is the pass state, every other choice is a failure.

    ``reverse_output`` flips the final bit. Used by evals where ``Pass`` from
    the LLM means the undesired condition WAS detected (e.g. hallucination
    detectors that emit ``Pass`` on hit).
    """
    choices = choices or []

    if output_type == "Pass/Fail":
        failure = str(result_value).strip().lower() in ("fail", "failed", "false", "0")
    elif output_type in ("score", "numeric"):
        try:
            failure = float(result_value) < pass_threshold
        except (ValueError, TypeError):
            failure = True
    elif output_type == "choices" and choices:
        if multi_choice and isinstance(result_value, list):
            actual_list = [str(c).strip().lower() for c in result_value]
            if choice_scores:
                scores_lower = {
                    str(k).strip().lower(): v for k, v in choice_scores.items()
                }
                picked: list[float] = []
                for a in actual_list:
                    v = scores_lower.get(a)
                    if v is not None:
                        try:
                            picked.append(float(v))
                        except (ValueError, TypeError):
                            pass
                if not picked:
                    failure = True
                else:
                    failure = (sum(picked) / len(picked)) < pass_threshold
            else:
                first = str(choices[0]).strip().lower()
                failure = any(a != first for a in actual_list)
        else:
            actual = str(result_value).strip().lower()
            if choice_scores:
                score = None
                for key, val in choice_scores.items():
                    if str(key).strip().lower() == actual:
                        try:
                            score = float(val)
                        except (ValueError, TypeError):
                            score = None
                        break
                failure = True if score is None else score < pass_threshold
            else:
                first = str(choices[0]).strip().lower()
                failure = actual != first
    else:
        failure = False

    if reverse_output:
        failure = not failure
    return failure


__all__ = ["build_eval_result", "compute_eval_failure"]
