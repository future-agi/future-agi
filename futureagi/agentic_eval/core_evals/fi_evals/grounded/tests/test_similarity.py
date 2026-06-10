"""Tests for grounded similarity comparators.

Regression coverage for #653: ``JaccardSimilarity`` and ``SorensenDiceSimilarity``
must do case-insensitive comparisons so they stay consistent with
``CosineSimilarity`` (which already lowercases via its tokenizer).
"""

import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    CosineSimilarity,
    JaccardSimilarity,
    SorensenDiceSimilarity,
)


class TestJaccardSimilarityCaseInsensitive:
    """JaccardSimilarity should ignore case when comparing token sets."""

    def test_identical_strings_differing_only_in_case_are_fully_similar(self):
        comparator = JaccardSimilarity()
        assert comparator.compare("Hello World", "hello world") == 1.0

    def test_uppercase_vs_lowercase_tokens_are_equal(self):
        comparator = JaccardSimilarity()
        assert comparator.compare("FOO BAR", "foo bar") == 1.0

    def test_mixed_case_overlap_is_measured_consistently(self):
        comparator = JaccardSimilarity()
        # {"the", "quick", "fox"} vs {"the", "quick", "dog"}
        # intersection = 2, union = 4 -> 0.5
        assert comparator.compare("The Quick Fox", "the QUICK dog") == 0.5

    def test_matches_cosine_similarity_case_behavior(self):
        # Both comparators should treat case-only differences as identical.
        jaccard = JaccardSimilarity()
        cosine = CosineSimilarity()
        s1, s2 = "Apple Banana Cherry", "apple BANANA cherry"
        assert jaccard.compare(s1, s2) == pytest.approx(cosine.compare(s1, s2))


class TestSorensenDiceSimilarityCaseInsensitive:
    """SorensenDiceSimilarity should ignore case when comparing token sets."""

    def test_identical_strings_differing_only_in_case_are_fully_similar(self):
        comparator = SorensenDiceSimilarity()
        assert comparator.compare("Hello World", "hello world") == 1.0

    def test_uppercase_vs_lowercase_tokens_are_equal(self):
        comparator = SorensenDiceSimilarity()
        assert comparator.compare("FOO BAR", "foo bar") == 1.0

    def test_mixed_case_partial_overlap(self):
        comparator = SorensenDiceSimilarity()
        # {"the", "quick", "fox"} vs {"the", "quick", "dog"}
        # 2 * 2 / (3 + 3) = 4/6 = 2/3
        assert comparator.compare("The Quick Fox", "the QUICK dog") == pytest.approx(2 / 3)


class TestSimilarityEdgeCases:
    """Empty / whitespace-only inputs must not raise ZeroDivisionError.

    All three comparators (Cosine, Jaccard, Sorensen-Dice) now share the same
    edge-case contract so callers dispatching through ``GroundedEvaluator`` get
    consistent results regardless of which comparator they configure.
    """

    @pytest.mark.parametrize(
        "comparator",
        [CosineSimilarity(), JaccardSimilarity(), SorensenDiceSimilarity()],
        ids=["cosine", "jaccard", "sorensen_dice"],
    )
    def test_both_empty_strings_return_1_without_dividing_by_zero(self, comparator):
        # Pre-fix the Jaccard / Sorensen-Dice paths raised ZeroDivisionError
        # and CosineSimilarity silently returned 0; all three now return 1.0
        # for two identical-but-empty inputs.
        assert comparator.compare("", "") == 1.0

    @pytest.mark.parametrize(
        "comparator",
        [CosineSimilarity(), JaccardSimilarity(), SorensenDiceSimilarity()],
        ids=["cosine", "jaccard", "sorensen_dice"],
    )
    def test_both_whitespace_only_return_1_without_dividing_by_zero(self, comparator):
        assert comparator.compare("   ", "\t\n") == 1.0

    @pytest.mark.parametrize(
        "comparator",
        [CosineSimilarity(), JaccardSimilarity(), SorensenDiceSimilarity()],
        ids=["cosine", "jaccard", "sorensen_dice"],
    )
    def test_one_empty_one_nonempty_returns_zero(self, comparator):
        assert comparator.compare("", "hello world") == 0.0

    @pytest.mark.parametrize(
        "comparator",
        [CosineSimilarity(), JaccardSimilarity(), SorensenDiceSimilarity()],
        ids=["cosine", "jaccard", "sorensen_dice"],
    )
    def test_completely_disjoint_token_sets(self, comparator):
        assert comparator.compare("alpha beta", "gamma delta") == 0.0

    @pytest.mark.parametrize(
        "comparator",
        [CosineSimilarity(), JaccardSimilarity(), SorensenDiceSimilarity()],
        ids=["cosine", "jaccard", "sorensen_dice"],
    )
    def test_unicode_case_folding(self, comparator):
        # Python's str.lower() handles common unicode case pairs.
        assert comparator.compare("ÉCOLE café", "école CAFÉ") == 1.0


class TestCrossComparatorEdgeCaseConsistency:
    """Explicit guarantee: Cosine, Jaccard, and Sorensen-Dice agree on edge cases.

    Added in response to review feedback (#653 thread): empty-input semantics
    must not diverge across comparators, since ``GroundedEvaluator`` dispatches
    them interchangeably and callers may apply a single ``failure_threshold``
    across runs.
    """

    COMPARATORS = [
        ("cosine", CosineSimilarity()),
        ("jaccard", JaccardSimilarity()),
        ("sorensen_dice", SorensenDiceSimilarity()),
    ]

    def _all_return(self, inputs, expected):
        s1, s2 = inputs
        for name, comparator in self.COMPARATORS:
            actual = comparator.compare(s1, s2)
            assert actual == pytest.approx(expected), (
                f"{name}.compare({s1!r}, {s2!r}) = {actual}, expected {expected}"
            )

    def test_all_comparators_agree_on_both_empty(self):
        self._all_return(("", ""), 1.0)

    def test_all_comparators_agree_on_one_empty(self):
        self._all_return(("", "hello world"), 0.0)

    def test_all_comparators_agree_on_identical_nonempty(self):
        self._all_return(("hello world", "hello world"), 1.0)

    def test_all_comparators_agree_on_case_only_differences(self):
        self._all_return(("Hello WORLD", "hello world"), 1.0)
