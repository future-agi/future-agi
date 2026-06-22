"""
Tests for Phase 2: Scoring System utilities.

All unit tests — no database access needed.
"""

import math

import pytest

from model_hub.utils.scoring import (
    apply_choice_scores,
    determine_pass_fail,
    normalize_score,
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

    def test_deterministic_multi_element_list_takes_first(self):
        scores = {"Yes": 1.0, "No": 0.0}
        assert normalize_score(["Yes", "No"], "deterministic", scores) == 1.0

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
