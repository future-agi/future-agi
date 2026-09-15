"""
Tests for the string-similarity comparators in
``agentic_eval.core_evals.fi_evals.grounded.similarity``.

These focus on ``JaroWincklerSimilarity``, which previously produced
mathematically incorrect scores:

- Identical strings did not score 1.0 (e.g. "hello"/"hello" -> 0.9333),
  because the transposition pointer only advanced on a character mismatch,
  re-comparing already-matched positions and over-counting transpositions.
- Short identical strings scored 0.0 (e.g. "a"/"a"), because a negative
  match window skipped every comparison.

The corrected implementation matches the reference Jaro computation in
``fi_evals.function.functions.calculate_jaro_winkler_similarity`` (the Jaro
component, i.e. without the Winkler prefix bonus).
"""

import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    JaroWincklerSimilarity,
)


@pytest.fixture
def comparator():
    return JaroWincklerSimilarity()


class TestJaroWincklerSimilarityIdentical:
    """Identical strings must score exactly 1.0 (the regression this fixes)."""

    @pytest.mark.parametrize(
        "value",
        ["a", "ab", "abcd", "hello", "MARTHA", "a longer sentence here"],
    )
    def test_identical_strings_score_one(self, comparator, value):
        assert comparator.compare(value, value) == pytest.approx(1.0)


class TestJaroWincklerSimilarityKnownValues:
    """Canonical Jaro values (no Winkler prefix bonus)."""

    @pytest.mark.parametrize(
        "a, b, expected",
        [
            ("MARTHA", "MARHTA", 0.944444),  # textbook Jaro example
            ("DIXON", "DICKSONX", 0.766667),
            ("CRATE", "TRACE", 0.733333),
        ],
    )
    def test_known_pairs(self, comparator, a, b, expected):
        assert comparator.compare(a, b) == pytest.approx(expected, abs=1e-4)


class TestJaroWincklerSimilarityEdgeCases:
    def test_no_common_characters_scores_zero(self, comparator):
        assert comparator.compare("abc", "xyz") == pytest.approx(0.0)

    @pytest.mark.parametrize("a, b", [("", "x"), ("x", ""), ("", "")])
    def test_empty_string_scores_zero(self, comparator, a, b):
        assert comparator.compare(a, b) == pytest.approx(0.0)

    def test_score_is_symmetric(self, comparator):
        assert comparator.compare("MARTHA", "MARHTA") == pytest.approx(
            comparator.compare("MARHTA", "MARTHA")
        )

    @pytest.mark.parametrize(
        "a, b",
        [("MARTHA", "MARHTA"), ("DIXON", "DICKSONX"), ("hello", "world")],
    )
    def test_score_within_unit_interval(self, comparator, a, b):
        score = comparator.compare(a, b)
        assert 0.0 <= score <= 1.0
