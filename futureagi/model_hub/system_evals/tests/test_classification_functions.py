"""Precision/F-beta label resolution through the registered function evaluators."""

import json

import pytest

from agentic_eval.core_evals.fi_evals import FBetaScore, PrecisionScore
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_f_beta_score,
    calculate_precision_score,
)

SCORERS = pytest.mark.parametrize(
    "score",
    [calculate_precision_score, calculate_f_beta_score],
    ids=["precision", "fbeta"],
)
PAIRS = [
    ("yes", "no"),
    ("true", "false"),
    (1, 0),
    (1, -1),
    ("positive", "negative"),
    ("pass", "fail"),
    ("passed", "failed"),
    ("spam", "ham"),
    ("correct", "incorrect"),
    ("relevant", "irrelevant"),
]


@SCORERS
@pytest.mark.parametrize("positive, negative", PAIRS)
def test_known_pairs_keep_binary_policy_when_positive_is_observed(
    score, positive, negative
):
    # Both classes present: TP=1, FP=2, FN=0. Test semantic selection too.
    mixed = score([positive] * 3 + [negative] * 2, [positive] + [negative] * 4)
    expected = 1 / 3 if score is calculate_precision_score else 1 / 2
    assert mixed["result"] == pytest.approx(expected)
    assert f"positive='{str(positive).lower()}'" in mixed["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
@pytest.mark.parametrize("positive, negative", PAIRS)
@pytest.mark.parametrize(
    "encode",
    [
        lambda value: value,
        lambda value: [value] * 3,
        lambda value: json.dumps([value] * 3),
    ],
    ids=["scalar", "batch", "json"],
)
def test_registered_negative_only_agreement_uses_observed_class(
    evaluator, positive, negative, encode
):
    result = evaluator()._evaluate(output=encode(negative), expected=encode(negative))
    assert result["metrics"][0]["value"] == 1.0
    assert result["failure"] is False
    assert "macro over 1 classes" in result["reason"]
    # The fallback is only for omitted labels, not an explicitly absent positive.
    explicit = evaluator(positive_label=positive)._evaluate(
        output=encode(negative), expected=encode(negative)
    )
    assert explicit["metrics"][0]["value"] == 0.0
    assert explicit["failure"] is True
    assert f"positive='{str(positive).lower()}'" in explicit["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
@pytest.mark.parametrize("positive, negative", PAIRS)
@pytest.mark.parametrize(
    "false_positive", [True, False], ids=["false-positive", "false-negative"]
)
def test_registered_known_pair_mistakes_do_not_use_agreement_fallback(
    evaluator, positive, negative, false_positive
):
    output = [positive, negative] if false_positive else [negative, negative]
    expected = [negative, negative] if false_positive else [positive, negative]
    result = evaluator()._evaluate(output=output, expected=expected)
    assert result["metrics"][0]["value"] == 0.0
    assert result["failure"] is True
    assert f"positive='{str(positive).lower()}'" in result["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
@pytest.mark.parametrize("label", ["pass", "fail", "skip"])
def test_registered_pass_fail_skip_rows_agree(evaluator, label):
    result = evaluator()._evaluate(output=label, expected=label)
    assert result["metrics"][0]["value"] == 1.0
    assert result["failure"] is False


@SCORERS
@pytest.mark.parametrize(
    "output, expected, precision, fbeta, classes",
    [
        (["pass", "skip"], ["fail", "skip"], 1 / 3, 1 / 3, 3),
        (["no", "fail"], ["no", "fail"], 1.0, 1.0, 2),
        (["no", "skip", "skip"], ["no", "no", "skip"], 3 / 4, 2 / 3, 2),
    ],
)
def test_partially_overlapping_pairs_use_macro(
    score, output, expected, precision, fbeta, classes
):
    result = score(output, expected)
    assert result["result"] == pytest.approx(
        precision if score is calculate_precision_score else fbeta
    )
    assert f"macro over {classes} classes" in result["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
@pytest.mark.parametrize("label", [0, False, ""])
def test_registered_explicit_falsy_labels_keep_config_and_one_vs_rest(evaluator, label):
    configured = evaluator(positive_label=label)
    restored = evaluator(**configured.to_config())
    assert restored.to_config()["positive_label"] == label
    result = restored._evaluate(
        output=[label, label, "other"], expected=[label, "other", "other"]
    )
    assert result["metrics"][0]["value"] == pytest.approx(
        1 / 2 if evaluator is PrecisionScore else 2 / 3
    )
    assert result["failure"] is False
    assert f"positive='{str(label).lower()}'" in result["reason"]
    absent = restored._evaluate(output="no", expected="no")
    assert absent["metrics"][0]["value"] == 0.0
    assert absent["failure"] is True


@SCORERS
@pytest.mark.parametrize("encode", [list, json.dumps], ids=["list", "json"])
def test_unknown_binary_labels_use_macro_not_sorted_class(score, encode):
    result = score(
        encode(["zebra", "zebra", "ant", "ant"]), encode(["zebra", "ant", "ant", "ant"])
    )
    # Precision: (1/2 + 1)/2; F1: (2/3 + 4/5)/2, not F1 of mean P/R.
    expected = 3 / 4 if score is calculate_precision_score else 11 / 15
    assert result["result"] == pytest.approx(expected)
    assert "macro over 2 classes" in result["reason"]


@SCORERS
def test_multiclass_includes_predicted_only_and_unpredicted_classes(score):
    result = score(["a", "a", "b", "extra"], ["a", "b", "b", "c"])
    # a: TP=1 FP=1 FN=0; b: TP=1 FP=0 FN=1; c and extra: zero.
    expected = 3 / 8 if score is calculate_precision_score else 1 / 3
    assert result["result"] == pytest.approx(expected)
    assert "macro over 4 classes" in result["reason"]


@SCORERS
def test_a_third_label_prevents_known_pair_guessing(score):
    result = score(["yes", "yes", "other"], ["yes", "no", "other"])
    expected = 1 / 2 if score is calculate_precision_score else 5 / 9
    assert result["result"] == pytest.approx(expected)
    assert "macro over 3 classes" in result["reason"]


@SCORERS
def test_positive_label_can_appear_only_in_predictions(score):
    result = score(["correct", "incorrect"], ["incorrect", "incorrect"])
    assert result["result"] == 0.0
    assert "positive='correct'" in result["reason"]


@pytest.mark.parametrize("positive", [0, "0"])
def test_fbeta_explicit_zero_overrides_multiclass_default(positive):
    result = calculate_f_beta_score(
        [0, 0, -1, -1, 1], [0, -1, -1, -1, 1], positive_label=positive
    )
    assert result["result"] == pytest.approx(2 / 3)
    assert "positive='0'" in result["reason"]


@pytest.mark.parametrize("beta, expected", [(0.5, 145 / 198), (2.0, 65 / 84)])
def test_macro_averages_each_class_fbeta_with_requested_beta(beta, expected):
    result = calculate_f_beta_score(
        ["z", "z", "a", "a"], ["z", "a", "a", "a"], beta=beta
    )
    assert result["result"] == pytest.approx(expected)
    assert "macro over 2 classes" in result["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
def test_registered_evaluator_and_config_round_trip_keep_zero(evaluator):
    configured = evaluator(positive_label=0)
    restored = evaluator(**configured.to_config())
    assert restored.to_config()["positive_label"] == 0
    result = restored._evaluate(output=[0, 0, -1, -1, 1], expected=[0, -1, -1, -1, 1])
    expected = 1 / 2 if evaluator is PrecisionScore else 2 / 3
    assert result["metrics"][0]["value"] == pytest.approx(expected)
    assert "positive='0'" in result["reason"]


@pytest.mark.parametrize("evaluator", [PrecisionScore, FBetaScore])
def test_registered_default_exposes_macro_policy(evaluator):
    result = evaluator()._evaluate(
        output=["a", "a", "b", "extra"], expected=["a", "b", "b", "c"]
    )
    expected = 3 / 8 if evaluator is PrecisionScore else 1 / 3
    assert result["metrics"][0]["value"] == pytest.approx(expected)
    assert "macro over 4 classes" in result["reason"]


# Compatibility controls: these need not fail when the scoring fix is removed.
@SCORERS
@pytest.mark.parametrize("label", [False, "", "missing", "no"])
def test_explicit_labels_are_never_replaced_by_defaults(score, label):
    result = score(
        [label, label, "other"], [label, "other", "other"], positive_label=label
    )
    expected = 1 / 2 if score is calculate_precision_score else 2 / 3
    assert result["result"] == pytest.approx(expected)


@SCORERS
def test_case_insensitive_scalar_and_json_inputs(score):
    assert score("YES", "yes")["result"] == 1.0
    assert score('["YES", "NO"]', '["yes", "no"]')["result"] == 1.0
    assert score("unknown", "unknown")["result"] == 1.0


@SCORERS
def test_empty_mismatched_and_no_true_positive_inputs(score):
    assert score([], [])["result"] == 0.0
    assert score(["yes"], ["no", "yes"])["result"] == 0.0
    assert score(["a", "b"], ["b", "a"])["result"] == 0.0
    assert score(["no"], ["yes"], positive_label="yes")["result"] == 0.0
