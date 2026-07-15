"""
Regression tests for Average Precision / MAP with duplicate retrieved items.

``calculate_mean_average_precision`` incremented ``hits`` on every occurrence of
a relevant item while dividing by ``len(gt_set)`` — a count of *distinct* items.
Mixing the two conventions let AP exceed its 1.0 upper bound:

    gt=["chunk_7"], retrieved=["chunk_7", "chunk_7", "chunk_3"]  ->  AP 2.0
    gt=["a"],       retrieved=["a"] * 5                          ->  AP 5.0

AP is bounded [0, 1] under every convention: the numerator can credit at most
|R| distinct documents and the denominator is |R|. Duplicate retrievals are
exactly what MAP is run to catch (hybrid retrievers, multi-query fan-out,
overlapping chunk windows), and ``mean_average_precision.yaml`` renders the
score as a percentage against ``pass_threshold: 0.5`` — so a duplicate-heavy
retriever displayed as 200% and unconditionally passed its own gate.

The sibling evaluators in this module were already correct on the same input
(``ndcg_at_k`` 1.0, ``precision_at_k`` 0.333, ``recall_at_k`` 1.0); MAP was the
only outlier. ``ndcg_at_k`` even carries the rule in a comment — "Repeated
retrieval of the same relevant item must not increase DCG" — and the fix applies
that same ``seen`` guard here.
"""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_mean_average_precision,
    ndcg_at_k,
)


class TestAveragePrecisionIsBounded:
    """AP is a bounded [0,1] quantity regardless of what the retriever returns."""

    @pytest.mark.parametrize(
        "ground_truth, retrieved",
        [
            (["chunk_7"], ["chunk_7", "chunk_7", "chunk_3"]),  # previously 2.0
            (["a"], ["a"] * 5),  # previously 5.0
            (["a", "b"], ["a", "a", "b", "b"]),
            (["a"], ["a", "a"]),
        ],
    )
    def test_duplicates_never_push_ap_above_one(self, ground_truth, retrieved):
        assert (
            calculate_mean_average_precision(ground_truth, retrieved)["result"] <= 1.0
        )

    def test_a_repeated_perfect_hit_scores_exactly_one(self):
        result = calculate_mean_average_precision(
            ["chunk_7"], ["chunk_7", "chunk_7", "chunk_3"]
        )
        assert result["result"] == pytest.approx(1.0)

    def test_reason_counts_distinct_relevant_items_not_occurrences(self):
        """Previously reported '2 relevant' when one distinct relevant doc existed."""
        reason = calculate_mean_average_precision(
            ["chunk_7"], ["chunk_7", "chunk_7", "chunk_3"]
        )["reason"]
        assert "1 relevant" in reason

    def test_agrees_with_the_ndcg_sibling_on_duplicate_input(self):
        """Both are binary-relevance metrics; a repeat must not credit either."""
        gt, ret = ["chunk_7"], ["chunk_7", "chunk_7", "chunk_3"]
        assert calculate_mean_average_precision(gt, ret)["result"] == pytest.approx(
            float(ndcg_at_k(gt, ret)["result"])
        )

    def test_duplicates_do_not_beat_a_clean_ranking(self):
        """Padding a result list with repeats must never improve the score."""
        clean = calculate_mean_average_precision(["a", "b"], ["a", "b"])["result"]
        padded = calculate_mean_average_precision(["a", "b"], ["a", "a", "b", "b"])[
            "result"
        ]
        assert padded <= clean


class TestNestedMultiQuery:
    """The nested (per-query) path has the same loop and the same defect."""

    def test_duplicates_in_one_query_do_not_inflate_map(self):
        result = calculate_mean_average_precision(
            [["a"], ["b"]], [["a", "a"], ["x", "b"]]
        )
        assert result["result"] <= 1.0
        # Query 1 is a perfect hit (1.0); query 2 finds 'b' at rank 2 (0.5).
        assert result["result"] == pytest.approx(0.75)

    def test_nested_reason_still_reports_query_count(self):
        reason = calculate_mean_average_precision([["a"], ["b"]], [["a"], ["b"]])[
            "reason"
        ]
        assert "2 queries" in reason


class TestBehaviourPreserved:
    """Duplicate-free input already scored correctly and must not move."""

    @pytest.mark.parametrize(
        "ground_truth, retrieved, expected_ap",
        [
            (["a"], ["a"], 1.0),
            (["a", "b"], ["a", "b"], 1.0),
            (
                ["a", "b"],
                ["b", "a"],
                1.0,
            ),  # AP is order-insensitive when all hit at top
            (["a", "b"], ["x", "a", "y", "b"], 0.5),
            (["a"], ["x", "y", "a"], 1.0 / 3),
            (["a"], ["x", "y"], 0.0),
        ],
    )
    def test_duplicate_free_scores_are_unchanged(
        self, ground_truth, retrieved, expected_ap
    ):
        assert calculate_mean_average_precision(ground_truth, retrieved)[
            "result"
        ] == pytest.approx(expected_ap)

    def test_earlier_hits_still_score_higher(self):
        """AP's core property: rank position matters."""
        early = calculate_mean_average_precision(["a"], ["a", "x", "y"])["result"]
        late = calculate_mean_average_precision(["a"], ["x", "y", "a"])["result"]
        assert early > late

    def test_missing_ground_truth_is_still_rejected(self):
        result = calculate_mean_average_precision([], ["a"])
        assert result["result"] == 0.0
        assert "Empty ground truth" in result["reason"]

    def test_irrelevant_duplicates_are_still_ignored(self):
        """Repeating a non-relevant item must not change the score either."""
        assert calculate_mean_average_precision(["a"], ["z", "z", "a"])[
            "result"
        ] == pytest.approx(1.0 / 3)
