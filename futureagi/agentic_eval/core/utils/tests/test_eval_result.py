"""Exhaustive tests for ``compute_eval_failure`` — the single source of
truth for failure-bit derivation across every LLM-as-judge evaluator.

Covers every (output_type × choice_scores × multi_choice × reverse_output
× pass_threshold) combination plus all known edge cases: case sensitivity,
whitespace, unicode, unparseable values, unknown labels, empty inputs,
None values, boolean coercion, partial-resolve aggregation.
"""

import pytest

from agentic_eval.core.utils.eval_result import (
    build_eval_result,
    compute_eval_failure,
)


# ──────────────────────────────────────────────────────────────────────
# Pass/Fail — value variants
# ──────────────────────────────────────────────────────────────────────


class TestPassFailValues:
    @pytest.mark.parametrize(
        "value,expected",
        [
            # canonical
            ("Pass", False),
            ("Fail", True),
            # case insensitive
            ("pass", False),
            ("PASS", False),
            ("fail", True),
            ("FAIL", True),
            # variants in fail-set
            ("Failed", True),
            ("FAILED", True),
            ("failed", True),
            ("false", True),
            ("FALSE", True),
            ("0", True),
            # whitespace
            (" Pass ", False),
            (" Fail ", True),
            ("\tFail\n", True),
            # everything outside fail-set is treated as pass
            ("Pass with caveat", False),
            ("Maybe", False),
            ("OK", False),
            ("Yes", False),
            ("True", False),
            ("1", False),
            ("Passed", False),
            ("PASSED", False),
            ("", False),  # empty defaults to pass
        ],
    )
    def test_passfail_str_values(self, value, expected):
        assert (
            compute_eval_failure(output_type="Pass/Fail", result_value=value)
            is expected
        )

    @pytest.mark.parametrize("value", [None, [], {}, object()])
    def test_passfail_non_str_values(self, value):
        # Anything not in fail-set after str() lower-cases is treated as pass.
        # None → "none" → not in fail-set → pass.
        result = compute_eval_failure(output_type="Pass/Fail", result_value=value)
        assert result in (True, False)  # never raises

    @pytest.mark.parametrize(
        "value,reverse,expected",
        [
            ("Pass", False, False),
            ("Pass", True, True),
            ("Fail", False, True),
            ("Fail", True, False),
            ("Failed", True, False),
            ("PASS", True, True),
        ],
    )
    def test_passfail_with_reverse(self, value, reverse, expected):
        assert (
            compute_eval_failure(
                output_type="Pass/Fail", result_value=value, reverse_output=reverse
            )
            is expected
        )


# ──────────────────────────────────────────────────────────────────────
# score / numeric — full threshold × value matrix
# ──────────────────────────────────────────────────────────────────────


class TestScoreNumericValues:
    @pytest.mark.parametrize("output_type", ["score", "numeric"])
    @pytest.mark.parametrize(
        "value,threshold,expected",
        [
            # canonical
            (0.8, 0.5, False),
            (0.3, 0.5, True),
            # boundaries — strict `<`
            (0.5, 0.5, False),  # equal → not less → pass
            (0.5000001, 0.5, False),
            (0.4999999, 0.5, True),
            # thresholds 0.0 and 1.0
            (0.0, 0.0, False),  # 0 not < 0
            (0.1, 0.0, False),
            (0.0, 1.0, True),
            (0.99, 1.0, True),
            (1.0, 1.0, False),  # equal at 1
            # custom thresholds
            (0.7, 0.8, True),
            (0.7, 0.6, False),
            (0.3, 0.3, False),
            (0.29, 0.3, True),
            # int values
            (1, 0.5, False),
            (0, 0.5, True),
            # string-numeric coercion
            ("0.7", 0.5, False),
            ("0.3", 0.5, True),
            ("1", 0.5, False),
            (" 0.7 ", 0.5, False),  # str(float) handles whitespace
        ],
    )
    def test_score_threshold_matrix(self, output_type, value, threshold, expected):
        assert (
            compute_eval_failure(
                output_type=output_type,
                result_value=value,
                pass_threshold=threshold,
            )
            is expected
        )

    @pytest.mark.parametrize(
        "value",
        ["abc", "not a number", None, [], {}, "", "1.2.3", "[]"],
    )
    @pytest.mark.parametrize("output_type", ["score", "numeric"])
    def test_unparseable_fails_safe(self, output_type, value):
        assert (
            compute_eval_failure(output_type=output_type, result_value=value)
            is True
        )

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.8, True),  # pass→fail with reverse
            (0.3, False),  # fail→pass with reverse
            (0.5, True),  # equal-at-threshold reverses
            ("garbage", False),  # unparseable was True → reverses to False
        ],
    )
    def test_score_reverse(self, value, expected):
        assert (
            compute_eval_failure(
                output_type="score",
                result_value=value,
                pass_threshold=0.5,
                reverse_output=True,
            )
            is expected
        )

    @pytest.mark.parametrize(
        "value,expected_pass",
        [
            (float("inf"), False),  # inf > 0.5
            (float("-inf"), True),  # -inf < 0.5
            (True, False),  # bool(True) = 1.0 > 0.5
            (False, True),  # bool(False) = 0.0 < 0.5
        ],
    )
    def test_score_extreme_values(self, value, expected_pass):
        result = compute_eval_failure(
            output_type="score", result_value=value, pass_threshold=0.5
        )
        # NaN is its own special case — float("nan") < x is always False so
        # NaN would actually pass. Not asserted here; just covered by
        # not raising.
        assert result in (True, False)
        if value != float("nan"):
            assert result is expected_pass


# ──────────────────────────────────────────────────────────────────────
# choices — single, WITH choice_scores
# ──────────────────────────────────────────────────────────────────────


class TestSingleChoiceWithScores:
    CHOICES = ["Joy", "Neutral", "Sad"]
    SCORES = {"Joy": 1.0, "Neutral": 0.5, "Sad": 0.0}

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Joy", False),
            ("Neutral", False),  # 0.5 not < 0.5
            ("Sad", True),
            # case insensitive
            ("JOY", False),
            ("joy", False),
            ("SAD", True),
            ("sad", True),
            # whitespace
            (" joy ", False),
            ("  Sad  ", True),
            # unknown → fail safe
            ("Confused", True),
            ("", True),
            ("Sadness", True),  # not the same as "Sad"
        ],
    )
    def test_single_choice_lookup(self, label, expected):
        assert (
            compute_eval_failure(
                output_type="choices",
                result_value=label,
                choices=self.CHOICES,
                choice_scores=self.SCORES,
            )
            is expected
        )

    @pytest.mark.parametrize(
        "label,threshold,expected",
        [
            ("Neutral", 0.4, False),  # 0.5 > 0.4
            ("Neutral", 0.5, False),  # 0.5 == 0.5 → pass (strict <)
            ("Neutral", 0.6, True),  # 0.5 < 0.6 → fail
            ("Joy", 0.9, False),  # 1.0 > 0.9
            ("Joy", 1.0, False),  # 1.0 == 1.0 → pass
            ("Sad", 0.0, False),  # 0.0 == 0.0 → pass
            ("Sad", 0.01, True),  # 0.0 < 0.01 → fail
        ],
    )
    def test_single_choice_thresholds(self, label, threshold, expected):
        assert (
            compute_eval_failure(
                output_type="choices",
                result_value=label,
                choices=self.CHOICES,
                choice_scores=self.SCORES,
                pass_threshold=threshold,
            )
            is expected
        )

    @pytest.mark.parametrize(
        "label,reverse,expected",
        [
            ("Joy", False, False),
            ("Joy", True, True),
            ("Sad", False, True),
            ("Sad", True, False),
            ("Confused", False, True),  # unknown fails safe
            ("Confused", True, False),  # reverse of unknown → "pass"
        ],
    )
    def test_single_choice_reverse(self, label, reverse, expected):
        assert (
            compute_eval_failure(
                output_type="choices",
                result_value=label,
                choices=self.CHOICES,
                choice_scores=self.SCORES,
                reverse_output=reverse,
            )
            is expected
        )


# ──────────────────────────────────────────────────────────────────────
# choices — single, WITHOUT choice_scores (ordinal)
# ──────────────────────────────────────────────────────────────────────


class TestSingleChoiceOrdinal:
    @pytest.mark.parametrize(
        "choices,value,expected",
        [
            # binary
            (["Yes", "No"], "Yes", False),
            (["Yes", "No"], "No", True),
            (["Yes", "No"], "yes", False),
            (["Yes", "No"], "no", True),
            # ordinal scale
            (["never", "rarely", "sometimes", "always"], "never", False),
            (["never", "rarely", "sometimes", "always"], "rarely", True),
            (["never", "rarely", "sometimes", "always"], "always", True),
            # Pass/Fail-style
            (["Passed", "Failed"], "Passed", False),
            (["Passed", "Failed"], "Failed", True),
            # unicode labels
            (["✓", "✗"], "✓", False),
            (["✓", "✗"], "✗", True),
            # unknown is treated as not-first → fail
            (["Yes", "No"], "Maybe", True),
            (["Yes", "No"], "", True),
        ],
    )
    def test_ordinal_single(self, choices, value, expected):
        assert (
            compute_eval_failure(
                output_type="choices",
                result_value=value,
                choices=choices,
            )
            is expected
        )

    def test_ordinal_with_reverse(self):
        # No-scores ordinal: first choice passes, reverse inverts.
        assert compute_eval_failure(
            output_type="choices",
            result_value="No",
            choices=["Yes", "No"],
            reverse_output=True,
        ) is False
        assert compute_eval_failure(
            output_type="choices",
            result_value="Yes",
            choices=["Yes", "No"],
            reverse_output=True,
        ) is True


# ──────────────────────────────────────────────────────────────────────
# choices — multi-choice, WITH choice_scores (mean aggregation)
# ──────────────────────────────────────────────────────────────────────


class TestMultiChoiceWithScores:
    CHOICES = ["Joy", "Love", "Surprise", "Neutral", "Sadness", "Anger"]
    SCORES = {
        "Joy": 1.0, "Love": 1.0,
        "Surprise": 0.5, "Neutral": 0.5,
        "Sadness": 0.0, "Anger": 0.0,
    }

    @pytest.mark.parametrize(
        "labels,expected,reason",
        [
            (["Joy"], False, "single high"),
            (["Sadness"], True, "single low"),
            (["Joy", "Love"], False, "both high"),
            (["Sadness", "Anger"], True, "both low"),
            (["Joy", "Sadness"], False, "mean 0.5 == threshold → pass"),
            (["Joy", "Anger"], False, "mean 0.5 == threshold → pass"),
            (["Surprise", "Anger"], True, "mean 0.25 → fail"),
            (["Joy", "Love", "Surprise"], False, "mean 0.83 → pass"),
            (["Surprise", "Neutral"], False, "mean 0.5 → pass"),
            (["Sadness", "Anger", "Neutral"], True, "mean 0.17 → fail"),
            (["Joy", "Joy", "Sadness"], False, "duplicates: mean 0.67 → pass"),
        ],
    )
    def test_multi_choice_mean(self, labels, expected, reason):
        assert compute_eval_failure(
            output_type="choices",
            result_value=labels,
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
        ) is expected, f"failed: {reason}"

    @pytest.mark.parametrize(
        "labels,threshold,expected",
        [
            (["Joy", "Anger"], 0.5, False),  # mean 0.5 → pass at strict <
            (["Joy", "Anger"], 0.51, True),  # mean 0.5 → fail above
            (["Joy", "Anger"], 0.49, False),  # mean 0.5 → pass below
            (["Joy", "Joy"], 0.99, False),  # mean 1.0 → pass at 0.99
            (["Sadness", "Sadness"], 0.0, False),  # mean 0.0 → pass at 0
            (["Sadness", "Sadness"], 0.01, True),  # mean 0.0 → fail above 0
        ],
    )
    def test_multi_choice_threshold(self, labels, threshold, expected):
        assert compute_eval_failure(
            output_type="choices",
            result_value=labels,
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
            pass_threshold=threshold,
        ) is expected

    @pytest.mark.parametrize(
        "labels,expected",
        [
            (["MadeUp"], True),  # all unknown → fail safe
            (["MadeUp", "AlsoFake"], True),  # all unknown → fail safe
            (["Joy", "MadeUp"], False),  # partial: only Joy resolves, mean 1.0 → pass
            (["Sadness", "MadeUp"], True),  # partial: only Sadness, mean 0.0 → fail
            ([], True),  # empty list → fail safe (no recognised choices)
        ],
    )
    def test_multi_choice_unknown_labels(self, labels, expected):
        assert compute_eval_failure(
            output_type="choices",
            result_value=labels,
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
        ) is expected

    @pytest.mark.parametrize(
        "labels,expected",
        [
            (["JOY", "LOVE"], False),  # uppercase
            (["joy", "love"], False),  # lowercase
            (["Joy", "love", "JOY"], False),  # mixed case duplicates
            ([" Joy ", " Love "], False),  # whitespace
        ],
    )
    def test_multi_choice_case_whitespace(self, labels, expected):
        assert compute_eval_failure(
            output_type="choices",
            result_value=labels,
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
        ) is expected

    def test_multi_choice_reverse(self):
        assert compute_eval_failure(
            output_type="choices",
            result_value=["Joy", "Love"],
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
            reverse_output=True,
        ) is True


# ──────────────────────────────────────────────────────────────────────
# choices — multi-choice, WITHOUT choice_scores (ordinal)
# ──────────────────────────────────────────────────────────────────────


class TestMultiChoiceOrdinal:
    @pytest.mark.parametrize(
        "labels,choices,expected",
        [
            # Only the first ordinal choice → pass
            (["Yes"], ["Yes", "No"], False),
            (["Yes", "Yes"], ["Yes", "No"], False),  # all-first dupes
            # any non-first → fail
            (["No"], ["Yes", "No"], True),
            (["Yes", "No"], ["Yes", "No"], True),
            (["Maybe"], ["Yes", "No"], True),
            # case insensitive
            (["yes"], ["Yes", "No"], False),
            (["YES"], ["Yes", "No"], False),
            # 3-choice ordinal
            (["never"], ["never", "sometimes", "always"], False),
            (["sometimes"], ["never", "sometimes", "always"], True),
            (["never", "never"], ["never", "sometimes", "always"], False),
            (["never", "sometimes"], ["never", "sometimes", "always"], True),
        ],
    )
    def test_multi_choice_ordinal(self, labels, choices, expected):
        assert compute_eval_failure(
            output_type="choices",
            result_value=labels,
            choices=choices,
            multi_choice=True,
        ) is expected


# ──────────────────────────────────────────────────────────────────────
# Type coercion — multi_choice flag with various result_value types
# ──────────────────────────────────────────────────────────────────────


class TestMultiChoiceTypeCoercion:
    """``multi_choice=True`` with a non-list result_value should fall back to
    the single-choice path rather than crashing."""

    CHOICES = ["A", "B"]
    SCORES = {"A": 1.0, "B": 0.0}

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("A", False),  # string → single-choice path
            ("B", True),
            ("a", False),
            ("Unknown", True),
        ],
    )
    def test_string_with_multi_choice_flag(self, value, expected):
        assert compute_eval_failure(
            output_type="choices",
            result_value=value,
            choices=self.CHOICES,
            choice_scores=self.SCORES,
            multi_choice=True,
        ) is expected


# ──────────────────────────────────────────────────────────────────────
# Edge cases / defaults / unknown output_types
# ──────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.parametrize(
        "output_type,value,expected",
        [
            ("reason", "any text", False),
            ("text", "any text", False),
            ("custom_unknown", 0.7, False),  # unknown defaults to False
            ("Pass/Fail", "Pass", False),  # canonical
        ],
    )
    def test_unknown_output_type_defaults_false(self, output_type, value, expected):
        assert compute_eval_failure(
            output_type=output_type, result_value=value
        ) is expected

    @pytest.mark.parametrize("output_type", ["reason", "text", "custom_unknown"])
    def test_unknown_output_type_with_reverse(self, output_type):
        # Even default-false branch honours reverse_output.
        assert compute_eval_failure(
            output_type=output_type,
            result_value="x",
            reverse_output=True,
        ) is True

    def test_empty_choices_falls_through(self):
        # choices type with no choices declared → falls through to default False.
        assert compute_eval_failure(
            output_type="choices",
            result_value="anything",
            choices=[],
        ) is False

    def test_none_choices_falls_through(self):
        assert compute_eval_failure(
            output_type="choices",
            result_value="anything",
            choices=None,
        ) is False

    def test_reverse_inverts_default_false(self):
        """Empty-choices fall-through is False; reverse flips it to True."""
        assert compute_eval_failure(
            output_type="choices",
            result_value="anything",
            choices=[],
            reverse_output=True,
        ) is True

    def test_choice_scores_none_with_choices(self):
        # No choice_scores → ordinal fallback
        assert compute_eval_failure(
            output_type="choices",
            result_value="Yes",
            choices=["Yes", "No"],
            choice_scores=None,
        ) is False

    def test_choice_scores_empty_dict_with_choices(self):
        # Empty dict is falsy → ordinal fallback
        assert compute_eval_failure(
            output_type="choices",
            result_value="Yes",
            choices=["Yes", "No"],
            choice_scores={},
        ) is False


# ──────────────────────────────────────────────────────────────────────
# Reverse inversion property — for every output_type, reverse must flip
# ──────────────────────────────────────────────────────────────────────


class TestReverseProperty:
    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(output_type="Pass/Fail", result_value="Pass"),
            dict(output_type="Pass/Fail", result_value="Fail"),
            dict(output_type="score", result_value=0.8),
            dict(output_type="score", result_value=0.2),
            dict(output_type="numeric", result_value=0.6),
            dict(
                output_type="choices",
                result_value="Joy",
                choices=["Joy", "Sad"],
                choice_scores={"Joy": 1.0, "Sad": 0.0},
            ),
            dict(
                output_type="choices",
                result_value="Sad",
                choices=["Joy", "Sad"],
                choice_scores={"Joy": 1.0, "Sad": 0.0},
            ),
            dict(
                output_type="choices",
                result_value=["Joy"],
                choices=["Joy", "Sad"],
                choice_scores={"Joy": 1.0, "Sad": 0.0},
                multi_choice=True,
            ),
        ],
    )
    def test_reverse_always_inverts(self, kwargs):
        base = compute_eval_failure(**kwargs)
        flipped = compute_eval_failure(reverse_output=True, **kwargs)
        assert base != flipped, (
            f"reverse_output failed to invert for kwargs={kwargs}: "
            f"base={base}, flipped={flipped}"
        )


# ──────────────────────────────────────────────────────────────────────
# build_eval_result envelope shape
# ──────────────────────────────────────────────────────────────────────


class TestBuildEvalResult:
    def test_all_keys_present(self):
        r = build_eval_result(
            name="test_eval",
            display_name="Test Eval",
            result_value=0.8,
            failure=False,
            explanation="looks good",
            runtime_ms=42,
            model="judge_model",
            metric_id="score",
            metadata="{}",
        )
        expected_keys = {
            "name", "display_name", "data", "failure", "metadata",
            "reason", "runtime", "model", "metrics",
            "datapoint_field_annotations",
        }
        assert set(r.keys()) == expected_keys

    def test_envelope_structure(self):
        r = build_eval_result(
            name="x", display_name="X", result_value=0.5, failure=False,
            explanation="why", runtime_ms=10, model="m", metric_id="mid",
            metadata="{}",
        )
        assert r["data"] == {"result": 0.5}
        assert r["metrics"] == [{"id": "mid", "value": 0.5}]
        assert r["datapoint_field_annotations"] is None

    @pytest.mark.parametrize(
        "value",
        [
            0.5,
            "Pass",
            ["Joy", "Love"],
            {"nested": "obj"},
            None,
            True,
            42,
        ],
    )
    def test_result_value_passthrough(self, value):
        r = build_eval_result(
            name="x", display_name="X", result_value=value, failure=False,
            explanation="", runtime_ms=0, model=None, metric_id="m",
            metadata="{}",
        )
        assert r["data"]["result"] == value
        assert r["metrics"][0]["value"] == value

    def test_datapoint_annotations_passthrough(self):
        annotations = {"field": "value"}
        r = build_eval_result(
            name="x", display_name="X", result_value=0.5, failure=False,
            explanation="", runtime_ms=0, model=None, metric_id="m",
            metadata="{}", datapoint_field_annotations=annotations,
        )
        assert r["datapoint_field_annotations"] == annotations

    def test_failure_bit_preserved(self):
        for failure_bit in (True, False):
            r = build_eval_result(
                name="x", display_name="X", result_value=0.5,
                failure=failure_bit, explanation="", runtime_ms=0,
                model=None, metric_id="m", metadata="{}",
            )
            assert r["failure"] is failure_bit
