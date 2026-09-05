"""Regression tests for positive-class resolution in precision / F-beta.

Both evaluators used to auto-select the positive class alphabetically, which
resolves to the *negative* member of every common binary convention (no, false,
0, negative, fail, ham). These tests pin the corrected behaviour.
"""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_f_beta_score,
    calculate_precision_score,
)

# A spam classifier over 10 samples: 2 truly spam, 3 predicted spam, 2 correct.
# Precision for "spam" is 2/3; precision for "ham" is 7/7 = 1.0.
SPAM_LABELS = ["spam", "spam", "ham", "ham", "ham", "ham", "ham", "ham", "ham", "ham"]
SPAM_PREDS = ["spam", "spam", "spam", "ham", "ham", "ham", "ham", "ham", "ham", "ham"]


class TestConventionalPositiveLabel:
    """Auto-detection must resolve to the positive member of a known pair."""

    def test_spam_ham_does_not_score_the_negative_class(self):
        # Regression: previously returned 1.0, the precision of "ham".
        result = calculate_precision_score(SPAM_PREDS, SPAM_LABELS)
        assert result["result"] == pytest.approx(2 / 3)
        assert "spam" in result["reason"]

    def test_auto_matches_explicit_positive_label(self):
        auto = calculate_precision_score(SPAM_PREDS, SPAM_LABELS)
        explicit = calculate_precision_score(
            SPAM_PREDS, SPAM_LABELS, positive_label="spam"
        )
        assert auto["result"] == pytest.approx(explicit["result"])

    @pytest.mark.parametrize(
        "positive,negative",
        [
            ("yes", "no"),
            ("true", "false"),
            ("1", "0"),
            ("positive", "negative"),
            ("pass", "fail"),
            ("spam", "ham"),
            ("relevant", "irrelevant"),
            ("correct", "incorrect"),
            ("valid", "invalid"),
        ],
    )
    def test_every_convention_resolves_to_the_positive_member(self, positive, negative):
        labels = [positive, positive, negative, negative]
        preds = [positive, positive, positive, negative]

        # For the positive class: TP=2, FP=1, FN=0 -> P=2/3, R=1.0, F1=0.8.
        # For the negative class: TP=1, FP=0, FN=1 -> P=1.0, R=0.5, F1=2/3.
        # The old alphabetical auto-detection picked the negative class in every
        # one of these pairs, so these two assertions pin the correct choice.
        precision = calculate_precision_score(preds, labels)
        assert precision["result"] == pytest.approx(
            2 / 3
        ), f"precision scored the wrong class for {positive}/{negative}"
        assert f"positive='{positive}'" in precision["reason"]

        f_beta = calculate_f_beta_score(preds, labels)
        assert f_beta["result"] == pytest.approx(
            0.8
        ), f"f_beta scored the wrong class for {positive}/{negative}"
        assert f"positive='{positive}'" in f_beta["reason"]

    def test_convention_is_case_insensitive(self):
        labels = ["Yes", "Yes", "No", "No"]
        preds = ["Yes", "Yes", "Yes", "No"]
        assert calculate_precision_score(preds, labels)["result"] == pytest.approx(
            2 / 3
        )

    def test_convention_resolves_from_union_of_labels_and_predictions(self):
        # "no" never appears in ground truth but is predicted; the pair is still
        # recognised, so precision is scored for "yes" (2 of 3 correct).
        labels = ["yes", "yes", "yes"]
        preds = ["yes", "yes", "no"]
        result = calculate_precision_score(preds, labels)
        assert result["result"] == pytest.approx(1.0)
        assert "positive='yes'" in result["reason"]


class TestExplicitPositiveLabel:
    def test_explicit_label_overrides_convention(self):
        result = calculate_precision_score(
            SPAM_PREDS, SPAM_LABELS, positive_label="ham"
        )
        assert result["result"] == pytest.approx(1.0)

    def test_falsy_integer_zero_is_honoured_by_precision(self):
        labels = ["0", "0", "1", "1"]
        preds = ["0", "0", "0", "1"]
        # positive_label=0 is falsy; it must not be discarded.
        result = calculate_precision_score(preds, labels, positive_label=0)
        assert result["result"] == pytest.approx(2 / 3)
        assert "positive='0'" in result["reason"]

    def test_falsy_integer_zero_is_honoured_by_f_beta(self):
        # Regression: `if positive_label` dropped an explicit 0 and fell back to
        # auto-detection, which scored class "1" instead.
        labels = ["0", "0", "1", "1"]
        preds = ["0", "0", "0", "1"]
        result = calculate_f_beta_score(preds, labels, positive_label=0)
        # P = 2/3, R = 1.0 -> F1 = 0.8
        assert result["result"] == pytest.approx(0.8)
        assert "positive='0'" in result["reason"]

    def test_falsy_string_zero_is_honoured(self):
        labels = ["0", "0", "1", "1"]
        preds = ["0", "0", "0", "1"]
        assert calculate_f_beta_score(preds, labels, positive_label="0")[
            "result"
        ] == pytest.approx(0.8)

    def test_unknown_positive_label_yields_zero_not_a_crash(self):
        result = calculate_precision_score(
            SPAM_PREDS, SPAM_LABELS, positive_label="phishing"
        )
        assert result["result"] == 0.0
        assert "no predictions" in result["reason"]


class TestAveragingFallback:
    """Label sets with no known convention must average, not guess a class."""

    def test_non_conventional_binary_macro_averages(self):
        labels = ["cat", "cat", "dog", "dog"]
        preds = ["cat", "cat", "dog", "cat"]
        # cat: 3 predicted, 2 correct -> 2/3 ; dog: 1 predicted, 1 correct -> 1.0
        result = calculate_precision_score(preds, labels)
        assert result["result"] == pytest.approx((2 / 3 + 1.0) / 2)
        assert "macro" in result["reason"]

    def test_multiclass_macro_is_label_order_independent(self):
        labels = ["alpha", "beta", "gamma", "alpha", "beta", "gamma"]
        preds = ["alpha", "beta", "alpha", "alpha", "gamma", "gamma"]
        baseline = calculate_precision_score(preds, labels)["result"]

        # Renaming the classes must not change the score. Under the old
        # alphabetical auto-detection it did.
        rename = {"alpha": "zeta", "beta": "alpha", "gamma": "beta"}
        renamed = calculate_precision_score(
            [rename[p] for p in preds], [rename[label] for label in labels]
        )["result"]
        assert renamed == pytest.approx(baseline)

    def test_f_beta_multiclass_macro_averages(self):
        labels = ["a", "b", "c", "a"]
        preds = ["a", "b", "c", "b"]
        # a: P=1.0 R=0.5 F1=2/3 ; b: P=0.5 R=1.0 F1=2/3 ; c: P=1.0 R=1.0 F1=1.0
        result = calculate_f_beta_score(preds, labels)
        assert result["result"] == pytest.approx((2 / 3 + 2 / 3 + 1.0) / 3)
        assert "macro" in result["reason"]


class TestExplicitAveraging:
    LABELS = ["a", "b", "c", "a"]
    PREDS = ["a", "b", "c", "b"]

    def test_micro_precision_equals_accuracy_for_single_label(self):
        result = calculate_precision_score(self.PREDS, self.LABELS, average="micro")
        assert result["result"] == pytest.approx(0.75)
        assert "micro" in result["reason"]

    def test_weighted_precision_uses_support(self):
        # supports: a=2, b=1, c=1 ; per-class precision a=1.0, b=0.5, c=1.0
        expected = (1.0 * 2 + 0.5 * 1 + 1.0 * 1) / 4
        result = calculate_precision_score(self.PREDS, self.LABELS, average="weighted")
        assert result["result"] == pytest.approx(expected)
        assert "weighted" in result["reason"]

    def test_binary_average_without_resolvable_label_is_rejected(self):
        result = calculate_precision_score(self.PREDS, self.LABELS, average="binary")
        assert result["result"] == 0.0
        assert "requires positive_label" in result["reason"]

    def test_binary_average_with_explicit_label_is_accepted(self):
        result = calculate_precision_score(
            self.PREDS, self.LABELS, average="binary", positive_label="a"
        )
        assert result["result"] == pytest.approx(1.0)

    def test_explicit_average_overrides_convention(self):
        # Labels match the spam/ham convention, but macro was asked for.
        result = calculate_precision_score(SPAM_PREDS, SPAM_LABELS, average="macro")
        assert result["result"] == pytest.approx((1.0 + 2 / 3) / 2)
        assert "macro" in result["reason"]

    @pytest.mark.parametrize(
        "scorer", [calculate_precision_score, calculate_f_beta_score]
    )
    def test_invalid_average_is_reported(self, scorer):
        result = scorer(SPAM_PREDS, SPAM_LABELS, average="nonsense")
        assert result["result"] == 0.0
        assert "Invalid average" in result["reason"]


class TestInputHandling:
    def test_json_string_inputs_are_parsed(self):
        result = calculate_precision_score(
            '["spam", "ham"]', '["spam", "spam"]', positive_label="spam"
        )
        assert result["result"] == pytest.approx(1.0)

    def test_length_mismatch_reports_both_lengths(self):
        result = calculate_precision_score(["yes"], ["yes", "no"])
        assert result["result"] == 0.0
        assert "1 preds vs 2 labels" in result["reason"]

    @pytest.mark.parametrize(
        "scorer", [calculate_precision_score, calculate_f_beta_score]
    )
    def test_none_input_is_handled_without_crashing(self, scorer):
        # f_beta's parser previously lacked a None branch and coerced it to the
        # literal label "none".
        result = scorer(None, None)
        assert result["result"] == 0.0

    def test_empty_inputs_return_zero(self):
        assert calculate_precision_score([], [])["result"] == 0.0

    def test_beta_weights_recall(self):
        labels = ["yes", "yes", "yes", "no"]
        preds = ["yes", "no", "no", "no"]
        # P = 1.0, R = 1/3
        f1 = calculate_f_beta_score(preds, labels, beta=1.0)["result"]
        f2 = calculate_f_beta_score(preds, labels, beta=2.0)["result"]
        assert f1 == pytest.approx(0.5)
        # Higher beta weights recall more, and recall is the weaker half here.
        assert f2 < f1

    def test_beta_zero_reduces_to_precision(self):
        labels = ["yes", "yes", "yes", "no"]
        preds = ["yes", "no", "no", "no"]
        assert calculate_f_beta_score(preds, labels, beta=0.0)[
            "result"
        ] == pytest.approx(1.0)

    def test_negative_beta_is_rejected(self):
        result = calculate_f_beta_score(SPAM_PREDS, SPAM_LABELS, beta=-1.0)
        assert result["result"] == 0.0
        assert "non-negative" in result["reason"]

    def test_non_numeric_beta_is_rejected(self):
        result = calculate_f_beta_score(SPAM_PREDS, SPAM_LABELS, beta="wide")
        assert result["result"] == 0.0
        assert "Invalid beta" in result["reason"]


class TestScoreRange:
    @pytest.mark.parametrize(
        "scorer", [calculate_precision_score, calculate_f_beta_score]
    )
    @pytest.mark.parametrize("average", [None, "macro", "micro", "weighted"])
    def test_result_stays_within_unit_interval(self, scorer, average):
        labels = ["a", "b", "c", "a", "b", "c", "a"]
        preds = ["a", "c", "c", "b", "b", "a", "a"]
        result = scorer(preds, labels, average=average)
        assert 0.0 <= result["result"] <= 1.0
