"""Regression tests for the deterministic Jaro-Winkler evaluator."""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_jaro_winkler_similarity,
)


def test_odd_transposition_count_is_floored_before_jaro_formula():
    """Jaro uses floor(mismatched matching characters / 2) transpositions."""
    result = calculate_jaro_winkler_similarity(
        "dadc",
        "acdcabbc",
        case_insensitive=False,
    )

    assert result["result"] == pytest.approx(0.5972222222222222)
    assert "Jaro=0.5972" in result["reason"]


@pytest.mark.parametrize(
    ("output", "expected", "score"),
    [
        ("MARTHA", "MARHTA", 0.9611111111111111),
        ("DIXON", "DICKSONX", 0.8133333333333332),
    ],
)
def test_canonical_jaro_winkler_examples_stay_stable(output, expected, score):
    result = calculate_jaro_winkler_similarity(
        output,
        expected,
        case_insensitive=False,
    )

    assert result["result"] == pytest.approx(score)


def test_exact_match_still_returns_perfect_score():
    result = calculate_jaro_winkler_similarity("hello", "hello")

    assert result == {"result": 1.0, "reason": "Jaro-Winkler: 1.0 (exact match)"}


def test_empty_input_still_returns_zero_score():
    result = calculate_jaro_winkler_similarity("", "hello")

    assert result == {"result": 0.0, "reason": "Jaro-Winkler: 0.0 (empty string)"}
