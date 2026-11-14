"""
Regression tests for Jaro-Winkler similarity (GH-4090).
Verifies that the transposition count is floored, matching standard
Levenshtein/rapidfuzz reference implementations.
"""

import pytest
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_jaro_winkler_similarity,
)


def test_odd_transpositions_floored():
    """
    Regression for #4090: odd number of out-of-order matches.
    'dadc' vs 'acdcabbc' has 3 matching characters with 3 mismatches.
    Transposition count is floor(3 / 2) = 1, yielding Jaro=0.5972.
    Without flooring, (matches - 1.5)/matches incorrectly produced 0.5417.
    """
    res = calculate_jaro_winkler_similarity("dadc", "acdcabbc")
    assert round(res["result"], 4) == 0.5972


def test_standard_transposition_martha():
    """Standard transposition test case ('martha' vs 'marhta')."""
    res = calculate_jaro_winkler_similarity("martha", "marhta")
    assert round(res["result"], 4) == 0.9611


def test_exact_match():
    res = calculate_jaro_winkler_similarity("enterprise", "enterprise")
    assert res["result"] == 1.0
    assert "exact match" in res["reason"]


def test_case_insensitivity():
    res_default = calculate_jaro_winkler_similarity("FutureAGI", "futureagi")
    assert res_default["result"] == 1.0

    res_strict = calculate_jaro_winkler_similarity(
        "FutureAGI", "futureagi", case_insensitive=False
    )
    assert res_strict["result"] < 1.0


def test_empty_inputs():
    assert calculate_jaro_winkler_similarity("", "")["result"] == 1.0
    assert calculate_jaro_winkler_similarity("test", "")["result"] == 0.0
    assert calculate_jaro_winkler_similarity("", "test")["result"] == 0.0


def test_prefix_boost():
    """Common prefix up to 4 characters increases the similarity score."""
    res_boosted = calculate_jaro_winkler_similarity("dixon", "dicksonx", prefix_weight=0.1)
    res_no_boost = calculate_jaro_winkler_similarity("dixon", "dicksonx", prefix_weight=0.0)
    assert res_boosted["result"] > res_no_boost["result"]
    assert round(res_boosted["result"], 4) == 0.8133


def test_reversed_inputs():
    """Order of arguments should produce symmetric similarity values."""
    res1 = calculate_jaro_winkler_similarity("dadc", "acdcabbc")
    res2 = calculate_jaro_winkler_similarity("acdcabbc", "dadc")
    assert round(res1["result"], 4) == round(res2["result"], 4)
