"""Regression tests for ``calculate_matthews_correlation``.

The multiclass branch previously computed a chance-corrected accuracy
``(accuracy - 1/k) / (1 - 1/k)`` under a uniform class prior, which is not the
Matthews Correlation Coefficient. It now uses the confusion-matrix formulation
(Gorodkin's R_K), matching ``sklearn.metrics.matthews_corrcoef``.
"""

import unittest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_matthews_correlation,
)


class MatthewsCorrelationMulticlassTests(unittest.TestCase):
    def test_constant_predictor_scores_chance_not_high(self):
        # Always predict the majority class on imbalanced 3-class data. A
        # constant predictor has zero correlation with the labels; true MCC is
        # 0.0 (normalized 0.5). The old proxy returned 0.85 here.
        preds = ["A"] * 10
        labels = ["A"] * 8 + ["B"] + ["C"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 0.5, places=6)

    def test_realistic_multiclass_matches_true_mcc(self):
        # True multiclass MCC = 0.7078 (normalized 0.8539); the old proxy
        # returned 0.7000 (normalized 0.8500).
        preds = ["A", "B", "B", "B", "C", "A", "A", "B", "C", "A"]
        labels = ["A", "A", "B", "B", "C", "C", "A", "B", "C", "A"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 0.8538880365655817, places=6)

    def test_anticorrelated_predictions_are_negative(self):
        # Systematically shifted labels -> negative correlation. The [0, 1]
        # normalization of a negative MCC must fall below 0.5.
        preds = ["B", "C", "A", "B", "C", "A"]
        labels = ["A", "B", "C", "A", "B", "C"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 0.25, places=6)  # MCC = -0.5

    def test_perfect_multiclass_is_one(self):
        preds = ["A", "B", "C", "A"]
        labels = ["A", "B", "C", "A"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 1.0, places=6)


class MatthewsCorrelationBinaryTests(unittest.TestCase):
    def test_binary_no_correlation(self):
        preds = ["A", "A", "B", "B"]
        labels = ["A", "B", "A", "B"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 0.5, places=6)  # MCC = 0.0

    def test_binary_perfect(self):
        preds = ["A", "B", "A", "B"]
        labels = ["A", "B", "A", "B"]
        result = calculate_matthews_correlation(preds, labels)["result"]
        self.assertAlmostEqual(result, 1.0, places=6)


class MatthewsCorrelationInputTests(unittest.TestCase):
    def test_length_mismatch_returns_zero(self):
        result = calculate_matthews_correlation(["A"], ["A", "B"])
        self.assertEqual(result["result"], 0.0)

    def test_empty_returns_zero(self):
        result = calculate_matthews_correlation([], [])
        self.assertEqual(result["result"], 0.0)


if __name__ == "__main__":
    unittest.main()
