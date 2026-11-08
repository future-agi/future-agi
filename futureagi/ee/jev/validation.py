"""Validate provider output without converting malformed results into scores."""

import math

from ee.jev.models import NormalizedAnswer


class JevResponseError(ValueError):
    def __init__(self, code="JEV_RESPONSE_INVALID"):
        self.code = code
        super().__init__(code)


def _require(condition):
    if not condition:
        raise JevResponseError()


def _number(value, maximum=1):
    return (
        type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum
    )


def _finite_tree(value):
    if isinstance(value, float):
        _require(math.isfinite(value))
    elif isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def validate_answer(
    question_type,
    answer,
    *,
    expected_labels=None,
    level_count=None,
    expected_levels=None,
    requested_model,
    actual_model,
):
    _require(isinstance(actual_model, str) and bool(actual_model.strip()))
    if requested_model == "jev-1.13.0" and actual_model != requested_model:
        raise JevResponseError("JEV_MODEL_MISMATCH")
    _require(isinstance(answer, dict) and answer.get("type") == question_type)
    _finite_tree(answer)
    if question_type == "noul":
        probability = answer.get("noul")
        _require(_number(probability))
        return NormalizedAnswer(question_type, probability=probability)
    _require(question_type in ("choice", "score"))
    if question_type == "choice":
        keys = set(expected_labels or [])
        _require(isinstance(answer.get("choice"), str) and answer["choice"] in keys)
    else:
        _require(isinstance(level_count, int) and 2 <= level_count <= 10)
        keys = {str(i) for i in range(level_count)}
        _require(_number(answer.get("score"), level_count - 1))
        legend = answer.get("legend")
        _require(isinstance(legend, dict) and set(legend) == keys)
        if expected_levels is not None:
            _require(
                legend == {str(i): level for i, level in enumerate(expected_levels)}
            )
        _require(
            all(isinstance(value, str) and value.strip() for value in legend.values())
        )
    probabilities = answer.get("probabilities")
    _require(isinstance(probabilities, dict) and set(probabilities) == keys)
    _require(all(_number(value) for value in probabilities.values()))
    _require(abs(sum(probabilities.values()) - 1) <= 1e-3)
    confidence = answer.get("confidence")
    _require(_number(confidence))
    return NormalizedAnswer(
        question_type,
        choice=answer.get("choice"),
        distribution=probabilities,
        confidence=confidence,
        raw_score=answer.get("score"),
        legend=answer.get("legend"),
        normalized_score=(
            answer["score"] / (level_count - 1) if question_type == "score" else None
        ),
    )


def validate_response(response, question_type, *, requested_model, **expected):
    _require(isinstance(response, dict))
    _finite_tree(response)
    answers = response.get("answers")
    _require(isinstance(answers, dict) and set(answers) == {"q1"})
    usage = response.get("usage")
    _require(isinstance(usage, dict))
    for name in ("input_tokens", "output_tokens"):
        _require(type(usage.get(name)) is int and usage[name] >= 0)
    return validate_answer(
        question_type,
        answers["q1"],
        requested_model=requested_model,
        actual_model=response.get("model"),
        **expected,
    )
