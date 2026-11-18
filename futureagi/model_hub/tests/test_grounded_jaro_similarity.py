"""The legacy grounded comparator scores plain Jaro, without a prefix bonus."""

import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import JaroWincklerSimilarity


@pytest.mark.parametrize("value", ["a", "hello"])
def test_identical_strings_score_one(value):
    assert JaroWincklerSimilarity().compare(value, value) == 1.0


@pytest.mark.parametrize(
    ("left", "right", "score"),
    [
        ("MARTHA", "MARHTA", 17 / 18),  # six matches and one transposition
        ("DIXON", "DICKSONX", 23 / 30),
        ("CRATE", "TRACE", 11 / 15),
    ],
)
def test_grounded_jaro_scores(left, right, score):
    assert JaroWincklerSimilarity().compare(left, right) == pytest.approx(score)
