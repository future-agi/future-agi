"""
Regression tests for #980: similarity comparators must not raise
ZeroDivisionError on empty or whitespace-only input.

Also covers #653: JaccardSimilarity and SorensenDiceSimilarity must be
case-insensitive, consistent with CosineSimilarity and the YAML-seeded
answer_similarity evaluator.
"""

import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    PhoneticSimilarity,
    SorensenDiceSimilarity,
)

# ─── Degenerate-input regression (#980) ─────────────────────────────────────

ALL_COMPARATORS = [
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    PhoneticSimilarity,
    SorensenDiceSimilarity,
]

DEGENERATE_PAIRS = [
    ("", ""),
    ("   ", ""),
    ("", "hello"),
    ("hello", ""),
    ("   ", "\t\n"),
    ("   ", "hello"),
    ("hello", "   "),
]

# Comparators that use token-based matching (split on whitespace)
TOKEN_COMPARATORS = [JaccardSimilarity, SorensenDiceSimilarity]

# Comparators that use character-level matching
CHAR_COMPARATORS = [NormalisedLevenshteinSimilarity, JaroWincklerSimilarity]


class TestNoZeroDivisionError:
    """Every comparator must return a float in [0, 1] for any input —
    never raise ZeroDivisionError."""

    @pytest.mark.parametrize("comparator_cls", ALL_COMPARATORS)
    @pytest.mark.parametrize("string1,string2", DEGENERATE_PAIRS)
    def test_degenerate_input_no_crash(self, comparator_cls, string1, string2):
        score = comparator_cls().compare(string1, string2)
        assert isinstance(score, (int, float))
        assert 0.0 <= score <= 1.0


class TestEmptyBothSides:
    """When both inputs are empty, token-based comparators return 1.0
    (both sides produce the same empty token set), matching the YAML-seeded
    answer_similarity evaluator's behaviour.  Character-level comparators
    may differ (Cosine returns 0, JaroWinkler returns 0.0) and are not
    changed."""

    @pytest.mark.parametrize("comparator_cls", TOKEN_COMPARATORS)
    def test_token_comparators_empty_both_return_one(self, comparator_cls):
        assert comparator_cls().compare("", "") == 1.0

    @pytest.mark.parametrize("comparator_cls", TOKEN_COMPARATORS)
    def test_token_comparators_whitespace_both_return_one(self, comparator_cls):
        """Whitespace-only strings produce no tokens on either side.
        Both produce the same empty token set, so they are identical
        (consistent with the YAML answer_similarity evaluator)."""
        assert comparator_cls().compare("   ", "\t\n") == 1.0

    def test_norm_levenshtein_empty_both_return_one(self):
        assert NormalisedLevenshteinSimilarity().compare("", "") == 1.0

    def test_norm_levenshtein_one_empty_return_zero(self):
        assert NormalisedLevenshteinSimilarity().compare("hello", "") == 0.0
        assert NormalisedLevenshteinSimilarity().compare("", "hello") == 0.0

    def test_norm_levenshtein_whitespace_does_not_crash(self):
        score = NormalisedLevenshteinSimilarity().compare("   ", "  ")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


class TestOneEmptyInput:
    """When one side is empty and the other is not, the score should be 0.0
    for all comparators — zero similarity.

    Token-based comparators are the exception when the non-empty side is
    whitespace-only: ``split()`` produces no tokens, so both sides end up
    with empty token sets (→ 1.0).  That case is covered by
    ``TestEmptyBothSides``."""

    @pytest.mark.parametrize("comparator_cls", ALL_COMPARATORS)
    def test_one_empty_nonempty_returns_zero(self, comparator_cls):
        assert comparator_cls().compare("hello", "") == 0.0
        assert comparator_cls().compare("", "hello") == 0.0


class TestCaseInsensitivity:
    """JaccardSimilarity and SorensenDiceSimilarity must be case-insensitive,
    consistent with CosineSimilarity and the YAML answer_similarity evaluator
    which uses .lower().split() for tokenization. (#653)"""

    @pytest.mark.parametrize("comparator_cls", TOKEN_COMPARATORS)
    def test_case_insensitive_token_comparators(self, comparator_cls):
        assert comparator_cls().compare("Hello World", "hello world") == 1.0

    @pytest.mark.parametrize("comparator_cls", TOKEN_COMPARATORS)
    def test_case_insensitive_partial_overlap(self, comparator_cls):
        """Case-insensitive partial overlap: 'Hello There' vs 'hello world'
        shares one token out of two distinct, regardless of case."""
        score = comparator_cls().compare("Hello There", "hello world")
        assert 0 < score < 1.0  # partial overlap, not full match

    def test_cosine_is_case_insensitive(self):
        """Baseline: CosineSimilarity is already case-insensitive."""
        assert CosineSimilarity().compare("Hello World", "hello world") == pytest.approx(
            1.0
        )

    @pytest.mark.parametrize("comparator_cls", TOKEN_COMPARATORS)
    def test_cross_comparator_case_consistency(self, comparator_cls):
        """Token-based comparators and CosineSimilarity must agree on
        case-insensitive matches."""
        s1, s2 = "Hello World", "hello world"
        assert comparator_cls().compare(s1, s2) == pytest.approx(
            CosineSimilarity().compare(s1, s2)
        )


class TestKnownScores:
    """Guard against silently changing real similarity scores for
    non-degenerate input."""

    def test_jaccard_partial_overlap(self):
        assert JaccardSimilarity().compare(
            "hello world", "hello there"
        ) == pytest.approx(1 / 3)

    def test_jaccard_identical(self):
        assert JaccardSimilarity().compare("the cat sat", "the cat sat") == 1.0

    def test_jaccard_no_overlap(self):
        assert JaccardSimilarity().compare("cat dog", "fish bird") == 0.0

    def test_sorensen_dice_partial_overlap(self):
        assert SorensenDiceSimilarity().compare(
            "hello world", "hello there"
        ) == pytest.approx(0.5)

    def test_sorensen_dice_identical(self):
        assert SorensenDiceSimilarity().compare(
            "the cat sat", "the cat sat"
        ) == 1.0

    def test_norm_levenshtein_identical(self):
        assert NormalisedLevenshteinSimilarity().compare("hello", "hello") == 1.0

    def test_norm_levenshtein_completely_different(self):
        assert NormalisedLevenshteinSimilarity().compare("abc", "xyz") == 0.0

    def test_norm_levenshtein_kitten_sitting(self):
        assert NormalisedLevenshteinSimilarity().compare(
            "kitten", "sitting"
        ) == pytest.approx(4 / 7)

    def test_norm_levenshtein_one_insertion(self):
        assert NormalisedLevenshteinSimilarity().compare("cat", "cats") == pytest.approx(
            0.75
        )
