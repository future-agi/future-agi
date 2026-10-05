"""Scoring rules shared by simulation call rows and database read models."""

from __future__ import annotations

import logging
import math
import reprlib
from dataclasses import dataclass
from typing import Any

from evaluations.engine.instance import resolve_pass_threshold
from simulate.models import SimulateEvalConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvalScoringSpec:
    output_type: str
    threshold: float | None
    reverse_output: bool
    choice_scores: dict[str, float]


@dataclass(frozen=True)
class EvalJudgement:
    outcome: str | None
    score: float | None


def _binding_setting(config: SimulateEvalConfig, name: str, fallback: Any) -> Any:
    runtime = config.config if isinstance(config.config, dict) else {}
    run_config = runtime.get("run_config")
    if isinstance(run_config, dict) and run_config.get(name) is not None:
        return run_config[name]
    if runtime.get(name) is not None:
        return runtime[name]
    return fallback


def resolve_eval_scoring_spec(config: SimulateEvalConfig) -> EvalScoringSpec:
    template = config.eval_template
    template_config = template.config if isinstance(template.config, dict) else {}
    output_type = template.output_type_normalized or (
        "pass_fail"
        if str(template_config.get("output") or "").lower() == "pass/fail"
        else "percentage"
    )
    choices = _binding_setting(config, "choice_scores", template.choice_scores or {})
    choice_scores = {}
    if isinstance(choices, dict):
        for label, value in choices.items():
            if isinstance(value, bool | int | float) and math.isfinite(float(value)):
                choice_scores[str(label).strip().lower()] = float(value)
    configured_threshold = _binding_setting(
        config, "pass_threshold", getattr(template, "pass_threshold", None)
    )
    try:
        threshold = resolve_pass_threshold(template, config.config)
    except (TypeError, ValueError, OverflowError):
        threshold = None
    if threshold is not None and (
        isinstance(configured_threshold, bool)
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        threshold = None
    return EvalScoringSpec(
        output_type=output_type,
        threshold=threshold,
        reverse_output=bool(
            _binding_setting(
                config, "reverse_output", template_config.get("reverse_output", False)
            )
        ),
        choice_scores=choice_scores,
    )


def warn_invalid_eval_threshold(
    config: SimulateEvalConfig, spec: EvalScoringSpec
) -> None:
    if spec.threshold is None:
        configured_threshold = _binding_setting(
            config,
            "pass_threshold",
            getattr(config.eval_template, "pass_threshold", None),
        )
        logger.warning(
            "Invalid pass_threshold %s for evaluation config %s; "
            "threshold-dependent scores remain unmeasured",
            reprlib.repr(configured_threshold)[:200],
            getattr(config, "id", None),
        )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _choice_score(value: Any, scores: dict[str, float]) -> float | None:
    if isinstance(value, dict):
        value = value.get("choices", value.get("choice"))
    if isinstance(value, str):
        return scores.get(value.strip().lower())
    if isinstance(value, list):
        # Multi-choice selections are sets: repeat labels do not add more votes.
        selected = {str(item).strip().lower() for item in value}
        picked = [scores[label] for label in selected if label in scores]
        return sum(picked) / len(picked) if picked else None
    return None


def _raw_score(value: Any, spec: EvalScoringSpec) -> float | None:
    if spec.output_type == "deterministic" and spec.choice_scores:
        choice = _choice_score(value, spec.choice_scores)
        if choice is not None:
            return max(0.0, min(1.0, choice))
    if isinstance(value, dict):
        value = next(
            (
                value[key]
                for key in ("score", "result", "output", "choice", "value")
                if value.get(key) is not None
            ),
            None,
        )
    if isinstance(value, str) and spec.output_type == "pass_fail":
        token = value.strip().lower()
        if token in {"pass", "true", "yes", "success", "successful"}:
            return 1.0
        if token in {"fail", "false", "no", "failure", "unsuccessful"}:
            return 0.0
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return 1.0 if value.strip().lower() == "true" else 0.0
    number = _number(value)
    if number is None:
        return None
    if spec.output_type == "pass_fail":
        return 1.0 if number > 0 else 0.0
    if number > 1:
        number /= 100.0
    return max(0.0, min(1.0, number))


def judge_stored_eval(eval_data: Any, spec: EvalScoringSpec) -> EvalJudgement:
    if not isinstance(eval_data, dict):
        return EvalJudgement(None, None)
    status = str(eval_data.get("status") or "").strip().lower()
    if status in {"error", "failed"}:
        return EvalJudgement("error", None)
    if status in {"pending", "skipped"}:
        return EvalJudgement(None, None)
    value = eval_data.get("output")
    stored_output_type = str(eval_data.get("output_type") or "").strip().lower()
    if spec.output_type == "pass_fail" or stored_output_type in {
        "pass/fail",
        "pass_fail",
    }:
        if isinstance(value, dict) and isinstance(value.get("failure"), bool):
            passed = not value["failure"]
            return EvalJudgement("passed" if passed else "failed", float(passed))
        if isinstance(value, str):
            final = value.strip().lower()
            if final in {"passed", "failed"}:
                return EvalJudgement(final, 1.0 if final == "passed" else 0.0)

    if spec.threshold is None:
        return EvalJudgement(None, None)

    score = _raw_score(value, spec)
    if score is None:
        return EvalJudgement(None, None)
    passed = score > 0 if spec.output_type == "pass_fail" else score >= spec.threshold
    if spec.reverse_output:
        passed = not passed
        score = 1.0 - score
    return EvalJudgement("passed" if passed else "failed", score)
