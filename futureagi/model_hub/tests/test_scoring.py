"""
Tests for Phase 2: Scoring System utilities.

All unit tests — no database access needed.
"""

import math

import pytest

from model_hub.utils.scoring import (
    apply_choice_scores,
    determine_pass_fail,
    extract_eval_value,
    normalize_score,
    score_eval_output,
    validate_choice_scores,
    validate_pass_threshold,
)

# =============================================================================
# normalize_score tests
# =============================================================================


@pytest.mark.unit
class TestNormalizeScore:
    def test_pass_fail_passed_string(self):
        assert normalize_score("Passed", "pass_fail") == 1.0

    def test_pass_fail_failed_string(self):
        assert normalize_score("Failed", "pass_fail") == 0.0

    def test_pass_fail_bool_true(self):
        assert normalize_score(True, "pass_fail") == 1.0

    def test_pass_fail_bool_false(self):
        assert normalize_score(False, "pass_fail") == 0.0

    def test_pass_fail_none(self):
        assert normalize_score(None, "pass_fail") == 0.0

    def test_percentage_float(self):
        assert normalize_score(0.75, "percentage") == 0.75

    def test_percentage_clamped_high(self):
        assert normalize_score(1.5, "percentage") == 1.0

    def test_percentage_clamped_low(self):
        assert normalize_score(-0.5, "percentage") == 0.0

    def test_percentage_string_number(self):
        assert normalize_score("0.8", "percentage") == 0.8

    def test_percentage_invalid_string(self):
        assert normalize_score("not_a_number", "percentage") == 0.0

    def test_deterministic_with_choice_scores(self):
        scores = {"Yes": 1.0, "No": 0.0, "Maybe": 0.5}
        assert normalize_score("Yes", "deterministic", scores) == 1.0

    def test_deterministic_with_choice_scores_maybe(self):
        scores = {"Yes": 1.0, "No": 0.0, "Maybe": 0.5}
        assert normalize_score("Maybe", "deterministic", scores) == 0.5

    def test_deterministic_unknown_choice(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score("Unknown", "deterministic", scores) == 0.0

    def test_deterministic_list_value(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score(["Yes"], "deterministic", scores) == 1.0

    def test_deterministic_no_choice_scores_fallback_float(self):
        assert normalize_score(0.7, "deterministic") == 0.7

    def test_none_value(self):
        assert normalize_score(None) == 0.0


@pytest.mark.unit
class TestNormalizeScoreRobustness:
    @pytest.mark.parametrize(
        "value,output_type,expected",
        [
            (float("nan"), "percentage", 0.0),
            (float("inf"), "percentage", 1.0),
            (float("-inf"), "percentage", 0.0),
            ("nan", "percentage", 0.0),
            ("inf", "percentage", 1.0),
            ("1e-3", "percentage", 0.001),
            (True, "percentage", 1.0),
            (False, "percentage", 0.0),
            ("", "percentage", 0.0),
            ("   ", "percentage", 0.0),
            ([0.9], "percentage", 0.0),
            ({"score": 0.9}, "percentage", 0.0),
        ],
    )
    def test_percentage_robustness(self, value, output_type, expected):
        assert normalize_score(value, output_type) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PASSED", 1.0),
            ("YES", 1.0),
            ("TRUE", 1.0),
            ("Failed", 0.0),
            ("no", 0.0),
            ("maybe", 0.0),
            ("", 0.0),
            (0, 0.0),
            (1, 1.0),
            (-5, 0.0),
            (float("nan"), 0.0),
            ([True], 0.0),
            ({"result": True}, 0.0),
            (None, 0.0),
        ],
    )
    def test_pass_fail_robustness(self, value, expected):
        assert normalize_score(value, "pass_fail") == expected

    def test_deterministic_empty_list(self):
        assert normalize_score([], "deterministic", {"Yes": 1.0}) == 0.0

    def test_deterministic_multi_element_list_averages_mapped_scores(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score(["Yes", "No"], "deterministic", scores) == 0.5

    def test_deterministic_multi_element_list_skips_unknown_labels(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score(["Yes", "Maybe"], "deterministic", scores) == 1.0

    def test_deterministic_choice_case_insensitive(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score("yes", "deterministic", scores) == 1.0
        assert normalize_score("YES", "deterministic", scores) == 1.0

    def test_deterministic_choice_with_whitespace(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score(" Yes ", "deterministic", scores) == 1.0

    def test_deterministic_dict_value_returns_zero(self):
        assert normalize_score({"choice": "Yes"}, "deterministic", {"Yes": 1.0}) == 0.0

    def test_deterministic_none_value(self):
        assert normalize_score(None, "deterministic", {"Yes": 1.0}) == 0.0

    @pytest.mark.parametrize(
        "output_type",
        ["unknown", "PassFail", "PERCENTAGE", "", None],
    )
    def test_unknown_output_type_falls_back_to_percentage(self, output_type):
        assert normalize_score(0.75, output_type) == 0.75
        assert normalize_score("not_a_number", output_type) == 0.0
        assert normalize_score(None, output_type) == 0.0

    def test_never_raises(self):
        weird_inputs = [
            float("nan"),
            float("inf"),
            float("-inf"),
            {"deeply": {"nested": "dict"}},
            [[1, 2], [3, 4]],
            object(),
            b"bytes",
        ]
        for value in weird_inputs:
            for output_type in ("pass_fail", "percentage", "deterministic", "unknown"):
                result = normalize_score(value, output_type, {"x": 0.5})
                assert isinstance(result, float)
                assert 0.0 <= result <= 1.0
                assert not math.isnan(result)


# =============================================================================
# determine_pass_fail tests
# =============================================================================


@pytest.mark.unit
class TestDeterminePassFail:
    def test_above_threshold(self):
        assert determine_pass_fail(0.7, 0.5) is True

    def test_below_threshold(self):
        assert determine_pass_fail(0.3, 0.5) is False

    def test_at_threshold(self):
        assert determine_pass_fail(0.5, 0.5) is True

    def test_zero_threshold(self):
        assert determine_pass_fail(0.0, 0.0) is True

    def test_one_threshold(self):
        assert determine_pass_fail(0.99, 1.0) is False

    def test_default_threshold(self):
        assert determine_pass_fail(0.5) is True
        assert determine_pass_fail(0.49) is False


# =============================================================================
# apply_choice_scores tests
# =============================================================================


@pytest.mark.unit
class TestApplyChoiceScores:
    def test_valid_choice(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert apply_choice_scores("Yes", scores) == 1.0

    def test_missing_choice(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert apply_choice_scores("Maybe", scores) is None

    def test_empty_label(self):
        scores = {"Yes": 1.0}
        assert apply_choice_scores("", scores) is None

    def test_none_scores(self):
        assert apply_choice_scores("Yes", None) is None

    def test_empty_scores(self):
        assert apply_choice_scores("Yes", {}) is None


# =============================================================================
# validate_choice_scores tests
# =============================================================================


@pytest.mark.unit
class TestValidateChoiceScores:
    def test_valid(self):
        scores = {"Yes": 1.0, "No": 0.0, "Maybe": 0.5}
        assert validate_choice_scores(scores) == []

    def test_empty_dict(self):
        errors = validate_choice_scores({})
        assert len(errors) == 1
        assert "must not be empty" in errors[0]

    def test_not_a_dict(self):
        errors = validate_choice_scores("not_a_dict")
        assert len(errors) == 1
        assert "must be a dictionary" in errors[0]

    def test_value_out_of_range_high(self):
        errors = validate_choice_scores({"Yes": 1.5})
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    def test_value_out_of_range_low(self):
        errors = validate_choice_scores({"No": -0.1})
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    def test_value_not_number(self):
        errors = validate_choice_scores({"Yes": "high"})
        assert len(errors) == 1
        assert "must be a number" in errors[0]

    def test_empty_key(self):
        errors = validate_choice_scores({"": 1.0})
        assert len(errors) == 1
        assert "non-empty string" in errors[0]

    def test_integer_values_accepted(self):
        """Integer values (0, 1) should be valid."""
        assert validate_choice_scores({"Yes": 1, "No": 0}) == []


# =============================================================================
# validate_pass_threshold tests
# =============================================================================


@pytest.mark.unit
class TestValidatePassThreshold:
    def test_valid_float(self):
        assert validate_pass_threshold(0.5) == []

    def test_valid_zero(self):
        assert validate_pass_threshold(0.0) == []

    def test_valid_one(self):
        assert validate_pass_threshold(1.0) == []

    def test_out_of_range_high(self):
        errors = validate_pass_threshold(1.5)
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    def test_out_of_range_low(self):
        errors = validate_pass_threshold(-0.1)
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    def test_not_a_number(self):
        errors = validate_pass_threshold("0.5")
        assert len(errors) == 1
        assert "must be a number" in errors[0]

    def test_integer_accepted(self):
        assert validate_pass_threshold(1) == []


@pytest.mark.unit
class TestExtractEvalValue:
    def test_non_dict_passes_through(self):
        assert extract_eval_value("good") == "good"
        assert extract_eval_value(0.87) == 0.87
        assert extract_eval_value(["yes"]) == ["yes"]
        assert extract_eval_value(None) is None

    def test_failure_true_becomes_false(self):
        assert extract_eval_value({"failure": True}) is False

    def test_failure_false_becomes_true(self):
        assert extract_eval_value({"failure": False}) is True

    def test_failure_takes_priority_over_score(self):
        assert extract_eval_value({"failure": True, "score": 0.9}) is False

    def test_score_key(self):
        assert extract_eval_value({"score": 0.3, "choice": "choice_1"}) == 0.3

    def test_result_key(self):
        assert extract_eval_value({"result": "good"}) == "good"

    def test_output_key(self):
        assert extract_eval_value({"output": 0.5}) == 0.5

    def test_choice_key(self):
        assert extract_eval_value({"choice": "good"}) == "good"

    def test_value_key(self):
        assert extract_eval_value({"value": 0.85}) == 0.85

    def test_priority_score_over_result(self):
        assert extract_eval_value({"score": 0.5, "result": 0.9}) == 0.5

    def test_unknown_keys_return_dict_unchanged(self):
        payload = {"foo": "bar"}
        assert extract_eval_value(payload) is payload


class _Template:
    def __init__(self, output_type_normalized="percentage", choice_scores=None):
        self.output_type_normalized = output_type_normalized
        self.choice_scores = choice_scores


class _EvalRunResult:
    def __init__(self, eval_results):
        self.eval_results = eval_results


class _FullTemplate:

    def __init__(
        self,
        output="score",
        choice_scores=None,
        multi_choice=False,
        choices=None,
        output_type_normalized="percentage",
    ):
        self.config = {"output": output, "eval_type_id": "LlmEvaluator"}
        self.choice_scores = choice_scores
        self.multi_choice = multi_choice
        self.choices = choices
        self.output_type_normalized = output_type_normalized


@pytest.mark.unit
class TestScoreEvalOutputFormatted:
    def test_choice_dict_maps_via_choice_scores(self):
        template = _Template(
            output_type_normalized="deterministic",
            choice_scores={"good": 1.0, "bad": 0.0},
        )
        assert score_eval_output({"score": 1.0, "choice": "good"}, template) == 1.0

    def test_bare_choice_string_maps_via_choice_scores(self):
        template = _Template(
            output_type_normalized="deterministic",
            choice_scores={"good": 1.0, "bad": 0.0, "ok": 0.5},
        )
        assert score_eval_output("ok", template) == 0.5

    def test_list_of_choices_averaged_via_choice_scores(self):
        template = _Template(
            output_type_normalized="deterministic",
            choice_scores={"good": 1.0, "bad": 0.0, "ok": 0.5},
        )
        assert score_eval_output(["good", "ok"], template) == 0.75

    def test_pass_fail_strings(self):
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output("Passed", template) == 1.0
        assert score_eval_output("Failed", template) == 0.0

    def test_pass_fail_dict_via_failure(self):
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output({"failure": False}, template) == 1.0
        assert score_eval_output({"failure": True}, template) == 0.0

    def test_numeric_clamps(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output(0.3, template) == 0.3
        assert score_eval_output(1.5, template) == 1.0
        assert score_eval_output(-0.5, template) == 0.0

    def test_none_returns_zero(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output(None, template) == 0.0


@pytest.mark.unit
class TestScoreEvalOutputRawRunResult:
    def test_raw_choice_output(self):
        template = _FullTemplate(
            output="choices",
            choice_scores={"good": 1.0, "bad": 0.0},
            output_type_normalized="deterministic",
        )
        run_result = _EvalRunResult(
            eval_results=[
                {
                    "data": {"choice": "good"},
                    "failure": False,
                    "reason": "",
                    "runtime": 0,
                    "model": "gpt-4o",
                    "metrics": [],
                    "metadata": {},
                }
            ]
        )
        assert score_eval_output(run_result, template) == 1.0

    def test_raw_multi_choice_list_output(self):
        template = _FullTemplate(
            output="choices",
            choice_scores={"good": 1.0, "bad": 0.0, "ok": 0.5},
            multi_choice=True,
            output_type_normalized="deterministic",
        )
        run_result = _EvalRunResult(
            eval_results=[
                {
                    "data": ["good", "ok"],
                    "failure": False,
                    "reason": "",
                    "runtime": 0,
                    "model": "gpt-4o",
                    "metrics": [],
                    "metadata": {},
                }
            ]
        )
        assert score_eval_output(run_result, template) == 0.75

    def test_raw_numeric_metric_output(self):
        template = _FullTemplate(output="score", output_type_normalized="percentage")
        run_result = _EvalRunResult(
            eval_results=[
                {
                    "data": None,
                    "failure": False,
                    "reason": "",
                    "runtime": 0,
                    "model": "gpt-4o",
                    "metrics": [{"value": 0.42}],
                    "metadata": {},
                }
            ]
        )
        assert score_eval_output(run_result, template) == 0.42

    def test_raw_pass_fail_output(self):
        template = _FullTemplate(
            output="Pass/Fail", output_type_normalized="pass_fail"
        )
        run_result = _EvalRunResult(
            eval_results=[
                {
                    "data": None,
                    "failure": True,
                    "reason": "",
                    "runtime": 0,
                    "model": "gpt-4o",
                    "metrics": [],
                    "metadata": {},
                }
            ]
        )
        assert score_eval_output(run_result, template) == 0.0

    def test_empty_eval_results_returns_default_score(self):
        template = _FullTemplate(output="score")
        assert score_eval_output(_EvalRunResult(eval_results=[]), template) == 0.0
        assert (
            score_eval_output(
                _EvalRunResult(eval_results=[]), template, default_score=0.5
            )
            == 0.5
        )

    def test_nested_list_first_element_unwrapped(self):
        template = _FullTemplate(output="score")
        run_result = _EvalRunResult(
            eval_results=[
                [
                    {
                        "data": None,
                        "failure": False,
                        "reason": "",
                        "runtime": 0,
                        "model": "gpt-4o",
                        "metrics": [{"value": 0.6}],
                        "metadata": {},
                    }
                ]
            ]
        )
        assert score_eval_output(run_result, template) == 0.6


@pytest.mark.unit
class TestScoreEvalOutputDefaultScore:
    def test_unparseable_string_returns_default(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output("maybe", template) == 0.0
        assert score_eval_output("maybe", template, default_score=0.5) == 0.5

    def test_pass_fail_unknown_string_returns_default(self):
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output("0.85", template, default_score=0.5) == 0.5

    def test_deterministic_unknown_choice_returns_default(self):
        template = _Template(
            output_type_normalized="deterministic",
            choice_scores={"good": 1.0, "bad": 0.0},
        )
        assert score_eval_output("maybe", template, default_score=0.5) == 0.5

    def test_none_returns_default(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output(None, template, default_score=0.5) == 0.5

    def test_empty_list_returns_default(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output([], template, default_score=0.5) == 0.5

    def test_dict_without_recognized_key_returns_default(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output({"foo": "bar"}, template, default_score=0.5) == 0.5

    def test_default_does_not_override_valid_zero(self):
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output("Failed", template, default_score=0.5) == 0.0

    def test_value_key_dict_scores_correctly(self):
        template = _Template(output_type_normalized="percentage")
        assert score_eval_output({"value": 0.85}, template) == 0.85

    def test_default_score_none_surfaces_unscoreable_as_none(self):
        # Composite runner opts into None so its exclusion branch fires.
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output("maybe", template, default_score=None) is None

    def test_default_score_none_leaves_valid_scores_intact(self):
        template = _Template(output_type_normalized="pass_fail")
        assert score_eval_output("Passed", template, default_score=None) == 1.0
        assert score_eval_output("Failed", template, default_score=None) == 0.0
