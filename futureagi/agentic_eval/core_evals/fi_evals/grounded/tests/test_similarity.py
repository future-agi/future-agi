import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    SorensenDiceSimilarity,
)

ALL_COMPARATORS = [
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    SorensenDiceSimilarity,
]

DEGENERATE_PAIRS = [
    ("", ""),
    ("   ", ""),
    ("", "hello"),
    ("\t\n", "  "),
]


class TestDegenerateInputs:
    """Regression tests: comparators must not raise ZeroDivisionError on empty or
    whitespace-only input (e.g. an empty model completion). All comparators return
    0.0 for such input, matching the existing CosineSimilarity / JaroWincklerSimilarity
    behaviour."""

    @pytest.mark.parametrize("comparator_cls", ALL_COMPARATORS)
    @pytest.mark.parametrize("string1,string2", DEGENERATE_PAIRS)
    def test_no_zero_division_on_degenerate_input(
        self, comparator_cls, string1, string2
    ):
        score = comparator_cls().compare(string1, string2)
        assert isinstance(score, (int, float))
        assert 0.0 <= score <= 1.0

    @pytest.mark.parametrize(
        "comparator_cls",
        [JaccardSimilarity, SorensenDiceSimilarity, NormalisedLevenshteinSimilarity],
    )
    def test_both_empty_returns_zero(self, comparator_cls):
        assert comparator_cls().compare("", "") == 0.0


class TestKnownScores:
    """Guard the non-empty behaviour so the degenerate-input fix cannot silently
    change real similarity scores."""

    def test_jaccard_partial_overlap(self):
        assert JaccardSimilarity().compare(
            "hello world", "hello there"
        ) == pytest.approx(1 / 3)

    def test_jaccard_identical(self):
        assert JaccardSimilarity().compare("the cat sat", "the cat sat") == 1.0

    def test_sorensen_dice_partial_overlap(self):
        assert SorensenDiceSimilarity().compare(
            "hello world", "hello there"
        ) == pytest.approx(0.5)

    def test_normalised_levenshtein_kitten_sitting(self):
        assert NormalisedLevenshteinSimilarity().compare(
            "kitten", "sitting"
        ) == pytest.approx(4 / 7)
