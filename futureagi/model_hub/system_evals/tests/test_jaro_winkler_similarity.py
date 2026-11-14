"""Regression tests for the YAML and library Jaro-Winkler evaluators."""

from __future__ import annotations

import builtins
from functools import cache
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_jaro_winkler_similarity,
)

FUNCTION_DIR = Path(__file__).resolve().parent.parent / "function"


@cache
def _load_eval(name):
    path = FUNCTION_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    namespace: dict = {}
    builtins.exec(compile(code, str(path), "exec"), namespace)
    return namespace["evaluate"]


def _jaro_yaml(output, expected):
    return _load_eval("jaro_winkler_similarity")(
        None, output, expected, None, case_insensitive=False
    )


def _answer_yaml(output, expected):
    return _load_eval("answer_similarity")(
        None,
        None,
        None,
        None,
        response=output,
        expected_response=expected,
        comparator="JaroWincklerSimilarity",
    )


def _library(output, expected):
    result = calculate_jaro_winkler_similarity(output, expected, case_insensitive=False)
    return {"score": result["result"], "reason": result["reason"]}


EVALUATORS = pytest.mark.parametrize(
    "evaluate",
    [_jaro_yaml, _answer_yaml, _library],
    ids=["jaro-yaml", "answer-yaml", "library"],
)


@EVALUATORS
@pytest.mark.parametrize(
    ("output", "expected", "score"),
    [
        ("dadc", "acdcabbc", 0.5972222222222222),
        ("acdcabbc", "dadc", 0.5972222222222222),
        # Four matches, three mismatches: one transposition, plus a prefix bonus.
        ("xdadc", "xacdcabbc", 0.6983333333333334),
    ],
)
def test_odd_transposition_count_is_floored_before_jaro_formula(
    evaluate, output, expected, score
):
    result = evaluate(output, expected)

    assert result["score"] == pytest.approx(score)
    assert f"{score:.4f}" in result["reason"]


@EVALUATORS
@pytest.mark.parametrize(
    ("output", "expected", "score"),
    [
        ("MARTHA", "MARHTA", 0.9611111111111111),
        ("DIXON", "DICKSONX", 0.8133333333333332),
    ],
)
def test_canonical_jaro_winkler_examples_stay_stable(evaluate, output, expected, score):
    assert evaluate(output, expected)["score"] == pytest.approx(score)


@EVALUATORS
def test_exact_match_still_returns_perfect_score(evaluate):
    assert evaluate("hello", "hello")["score"] == 1.0


@EVALUATORS
def test_empty_input_still_returns_zero_score(evaluate):
    assert evaluate("", "hello")["score"] == 0.0
