"""
Unit tests for scoring helper functions in model_hub/utils/scoring.py.

Covers:
- apply_choice_scores
- aggregate_choice_scores
- validate_choice_scores
- validate_pass_threshold
- normalize_score
- determine_pass_fail

These are pure functions — no mocks, no Django required.
"""
from __future__ import annotations

import pytest

from model_hub.utils.scoring import (
    aggregate_choice_scores,
    apply_choice_scores,
    determine_pass_fail,
    normalize_score,
    validate_choice_scores,
    validate_pass_threshold,
)


# ---------------------------------------------------------------------------
# apply_choice_scores
# ---------------------------------------------------------------------------
class TestApplyChoiceScores:
    @pytest.mark.parametrize(
        "label,mapping,expected",
        [
            # Exact match
            ("Yes", {"Yes": 1.0, "No": 0.0}, 1.0),
            ("No", {"Yes": 1.0, "No": 0.0}, 0.0),
            ("Maybe", {"Yes": 1.0, "No": 0.0, "Maybe": 0.5}, 0.5),
            # Case-insensitive fallback
            ("yes", {"Yes": 1.0, "No": 0.0}, 1.0),
            ("YES", {"Yes": 1.0, "No": 0.0}, 1.0),
            ("YeS", {"Yes": 1.0, "No": 0.0}, 1.0),
            ("no", {"Yes": 1.0, "No": 0.0}, 0.0),
            ("PASS", {"pass": 0.8}, 0.8),
            # Whitespace tolerance via case-insensitive path
            ("  yes  ", {"Yes": 1.0}, 1.0),
            ("\tyes\n", {"Yes": 1.0}, 1.0),
            # Single-element mapping
            ("only", {"only": 0.7}, 0.7),
            # Large mapping
            ("k5", {f"k{i}": i / 10.0 for i in range(10)}, 0.5),
            ("k9", {f"k{i}": i / 10.0 for i in range(10)}, 0.9),
            # Unicode labels
            ("café", {"café": 0.6}, 0.6),
            ("CAFÉ", {"café": 0.6}, 0.6),
            ("日本", {"日本": 1.0}, 1.0),
            # Special chars
            ("a-b_c", {"a-b_c": 0.4}, 0.4),
            ("hi!", {"hi!": 0.3}, 0.3),
            # Score boundary values
            ("zero", {"zero": 0.0}, 0.0),
            ("half", {"half": 0.5}, 0.5),
            ("one", {"one": 1.0}, 1.0),
        ],
    )
    def test_returns_expected_score(self, label, mapping, expected):
        assert apply_choice_scores(label, mapping) == expected

    @pytest.mark.parametrize(
        "label,mapping",
        [
            # Not in mapping
            ("Maybe", {"Yes": 1.0, "No": 0.0}),
            ("unknown", {"a": 0.5}),
            # Empty / None inputs
            ("", {"Yes": 1.0}),
            (None, {"Yes": 1.0}),
            ("Yes", {}),
            ("Yes", None),
            ("", {}),
            (None, None),
        ],
    )
    def test_returns_none(self, label, mapping):
        assert apply_choice_scores(label, mapping) is None

    def test_exact_match_takes_precedence_over_case_insensitive(self):
        # When both exact and lowercase keys exist, exact match wins
        mapping = {"Yes": 0.9, "yes": 0.1}
        assert apply_choice_scores("Yes", mapping) == 0.9
        assert apply_choice_scores("yes", mapping) == 0.1


# ---------------------------------------------------------------------------
# aggregate_choice_scores
# ---------------------------------------------------------------------------
class TestAggregateChoiceScores:
    @pytest.mark.parametrize(
        "labels,mapping,expected",
        [
            # All resolve
            (["Yes", "No"], {"Yes": 1.0, "No": 0.0}, 0.5),
            (["Yes", "Yes"], {"Yes": 1.0, "No": 0.0}, 1.0),
            (["No", "No"], {"Yes": 1.0, "No": 0.0}, 0.0),
            (["a", "b", "c"], {"a": 0.0, "b": 0.5, "c": 1.0}, 0.5),
            # Single label
            (["Yes"], {"Yes": 1.0, "No": 0.0}, 1.0),
            (["No"], {"Yes": 1.0, "No": 0.0}, 0.0),
            # All same
            (["a", "a", "a"], {"a": 0.4}, 0.4),
            # All zero / all one
            (["a", "b"], {"a": 0.0, "b": 0.0}, 0.0),
            (["a", "b"], {"a": 1.0, "b": 1.0}, 1.0),
            # Partial resolve — only resolved entries counted
            (["Yes", "Unknown"], {"Yes": 1.0, "No": 0.0}, 1.0),
            (["Yes", "No", "Unknown"], {"Yes": 1.0, "No": 0.0}, 0.5),
            # Case-insensitive across the list
            (["yes", "NO"], {"Yes": 1.0, "No": 0.0}, 0.5),
            (["YES", "yes", "Yes"], {"Yes": 0.8}, 0.8),
            # Duplicates weight the mean
            (["Yes", "Yes", "No"], {"Yes": 1.0, "No": 0.0}, pytest.approx(2 / 3)),
        ],
    )
    def test_aggregate_returns_mean(self, labels, mapping, expected):
        result = aggregate_choice_scores(labels, mapping)
        assert result == pytest.approx(expected)

    @pytest.mark.parametrize(
        "labels,mapping",
        [
            # No labels resolve
            (["Unknown", "Other"], {"Yes": 1.0, "No": 0.0}),
            (["x", "y", "z"], {"a": 0.5}),
            # Empty list
            ([], {"Yes": 1.0}),
            # Empty mapping
            (["Yes"], {}),
            # Both empty
            ([], {}),
            # None inputs
            (None, {"Yes": 1.0}),
            (["Yes"], None),
            (None, None),
        ],
    )
    def test_aggregate_returns_none(self, labels, mapping):
        assert aggregate_choice_scores(labels, mapping) is None


# ---------------------------------------------------------------------------
# validate_choice_scores
# ---------------------------------------------------------------------------
class TestValidateChoiceScores:
    @pytest.mark.parametrize(
        "scores",
        [
            {"a": 1.0, "b": 0.0},
            {"yes": 1.0, "no": 0.0, "maybe": 0.5},
            {"only": 0.5},
            {"a": 1, "b": 0},  # int values
            {"a": 0, "b": 1, "c": 0.5},
            {"x": 0.0},
            {"x": 1.0},
        ],
    )
    def test_valid_returns_empty(self, scores):
        assert validate_choice_scores(scores) == []

    @pytest.mark.parametrize(
        "scores",
        [None, "string", [], 42, 3.14, ("a", "b")],
    )
    def test_not_a_dict(self, scores):
        errors = validate_choice_scores(scores)
        assert errors == ["choice_scores must be a dictionary"]

    def test_empty_dict(self):
        errors = validate_choice_scores({})
        assert errors == ["choice_scores must not be empty"]

    @pytest.mark.parametrize(
        "scores",
        [
            {1: 0.5},
            {None: 0.5},
            {(1, 2): 0.5},
            {"": 0.5},  # empty string key
            {"  ": 0.5},  # whitespace-only string key
        ],
    )
    def test_non_string_or_empty_keys(self, scores):
        errors = validate_choice_scores(scores)
        assert len(errors) == 1
        assert "non-empty string" in errors[0]

    @pytest.mark.parametrize(
        "scores",
        [
            {"a": "not-a-number"},
            {"a": None},
            {"a": [0.5]},
            {"a": {"nested": 1}},
        ],
    )
    def test_non_numeric_values(self, scores):
        errors = validate_choice_scores(scores)
        assert len(errors) == 1
        assert "must be a number" in errors[0]

    @pytest.mark.parametrize(
        "value",
        [1.1, 2.0, 100.0, -0.1, -1.0, -100.0],
    )
    def test_value_out_of_range(self, value):
        errors = validate_choice_scores({"a": value})
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    def test_mixed_valid_and_invalid_reports_only_invalid(self):
        scores = {
            "valid1": 0.5,  # valid
            "valid2": 1.0,  # valid
            "bad_value": 2.0,  # out of range
            "bad_type": "nope",  # not numeric
        }
        errors = validate_choice_scores(scores)
        assert len(errors) == 2
        joined = "\n".join(errors)
        assert "bad_value" in joined
        assert "bad_type" in joined
        assert "valid1" not in joined
        assert "valid2" not in joined


# ---------------------------------------------------------------------------
# validate_pass_threshold
# ---------------------------------------------------------------------------
class TestValidatePassThreshold:
    @pytest.mark.parametrize(
        "threshold", [0.0, 0.5, 1.0, 0.25, 0.75, 0, 1, 0.001, 0.999]
    )
    def test_valid_threshold(self, threshold):
        assert validate_pass_threshold(threshold) == []

    @pytest.mark.parametrize(
        "threshold", [-0.1, 1.1, -1.0, 2.0, -100, 100]
    )
    def test_out_of_range(self, threshold):
        errors = validate_pass_threshold(threshold)
        assert len(errors) == 1
        assert "between 0 and 1" in errors[0]

    @pytest.mark.parametrize(
        "threshold", ["0.5", "1.0", None, [], {}, (0.5,), object()],
    )
    def test_wrong_type(self, threshold):
        errors = validate_pass_threshold(threshold)
        assert len(errors) == 1
        assert "must be a number" in errors[0]

    def test_bool_is_treated_as_number(self):
        # bool is a subclass of int in Python — both 0 and 1 are in range
        assert validate_pass_threshold(True) == []
        assert validate_pass_threshold(False) == []


# ---------------------------------------------------------------------------
# normalize_score
# ---------------------------------------------------------------------------
class TestNormalizeScore:
    # pass_fail ----------------------------------------------------------
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, 1.0),
            (False, 0.0),
            ("passed", 1.0),
            ("PASS", 1.0),
            ("pass", 1.0),
            ("Pass", 1.0),
            ("true", 1.0),
            ("TRUE", 1.0),
            ("yes", 1.0),
            ("YES", 1.0),
            ("fail", 0.0),
            ("failed", 0.0),
            ("no", 0.0),
            ("anything-else", 0.0),
            ("", 0.0),
            (1, 1.0),
            (5, 1.0),
            (0, 0.0),
            (-1, 0.0),
            (0.5, 1.0),  # > 0 -> 1.0
            (0.0, 0.0),
            ([], 0.0),  # not bool/str/number -> 0.0
            ({}, 0.0),
        ],
    )
    def test_pass_fail(self, value, expected):
        assert normalize_score(value, output_type="pass_fail") == expected

    def test_pass_fail_none(self):
        assert normalize_score(None, output_type="pass_fail") == 0.0

    # percentage ---------------------------------------------------------
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.5, 0.5),
            (1.0, 1.0),
            (0.0, 0.0),
            (1.5, 1.0),  # clamp up
            (2.0, 1.0),
            (100.0, 1.0),
            (-0.5, 0.0),  # clamp down
            (-1.0, 0.0),
            ("0.5", 0.5),
            ("1.0", 1.0),
            ("0", 0.0),
            ("bad", 0.0),
            ("", 0.0),
            (1, 1.0),
            (0, 0.0),
        ],
    )
    def test_percentage(self, value, expected):
        assert normalize_score(value, output_type="percentage") == expected

    def test_percentage_none(self):
        assert normalize_score(None, output_type="percentage") == 0.0

    # deterministic ------------------------------------------------------
    @pytest.mark.parametrize(
        "value,scores,expected",
        [
            ("Yes", {"Yes": 1.0, "No": 0.0}, 1.0),
            ("No", {"Yes": 1.0, "No": 0.0}, 0.0),
            ("yes", {"Yes": 1.0, "No": 0.0}, 1.0),  # case-insensitive
            ("unknown", {"Yes": 1.0, "No": 0.0}, 0.0),  # unknown -> 0.0
            (["Yes"], {"Yes": 1.0, "No": 0.0}, 1.0),  # list input
            (["No", "Yes"], {"Yes": 1.0, "No": 0.0}, 0.0),  # uses first
            (["unknown"], {"Yes": 1.0}, 0.0),
        ],
    )
    def test_deterministic_with_choices(self, value, scores, expected):
        assert (
            normalize_score(value, output_type="deterministic", choice_scores=scores)
            == expected
        )

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.5, 0.5),
            (1.5, 1.0),  # clamp
            (-0.5, 0.0),  # clamp
            ("0.7", 0.7),
            ("bad", 0.0),
        ],
    )
    def test_deterministic_without_choices_falls_back_to_float(self, value, expected):
        assert normalize_score(value, output_type="deterministic") == expected

    def test_deterministic_empty_list_falls_back(self):
        # Empty list with choice_scores -> falls through to float() which fails -> 0.0
        result = normalize_score([], output_type="deterministic", choice_scores={"a": 1.0})
        assert result == 0.0

    def test_deterministic_none(self):
        assert normalize_score(None, output_type="deterministic") == 0.0

    # fallback / unknown output_type ------------------------------------
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.5, 0.5),
            (1.5, 1.0),  # clamp
            (-0.5, 0.0),  # clamp
            ("0.7", 0.7),
            ("bad", 0.0),
            ([], 0.0),
            ({}, 0.0),
        ],
    )
    def test_unknown_output_type_falls_back(self, value, expected):
        assert normalize_score(value, output_type="something_weird") == expected

    def test_unknown_output_type_none(self):
        assert normalize_score(None, output_type="something_weird") == 0.0


# ---------------------------------------------------------------------------
# determine_pass_fail
# ---------------------------------------------------------------------------
class TestDeterminePassFail:
    @pytest.mark.parametrize(
        "score,threshold,expected",
        [
            # Exactly at threshold passes
            (0.5, 0.5, True),
            (1.0, 1.0, True),
            (0.0, 0.0, True),
            (0.75, 0.75, True),
            # Above threshold
            (0.6, 0.5, True),
            (1.0, 0.5, True),
            (0.51, 0.5, True),
            # Below threshold
            (0.4, 0.5, False),
            (0.0, 0.5, False),
            (0.49, 0.5, False),
            # threshold=0 always passes (score is in [0,1])
            (0.0, 0.0, True),
            (0.5, 0.0, True),
            (1.0, 0.0, True),
            # threshold=1.0 — only 1.0 passes
            (1.0, 1.0, True),
            (0.99, 1.0, False),
            (0.5, 1.0, False),
            (0.0, 1.0, False),
            # Default threshold (0.5)
        ],
    )
    def test_threshold_comparison(self, score, threshold, expected):
        assert determine_pass_fail(score, threshold) is expected

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.5, True),
            (0.51, True),
            (1.0, True),
            (0.49, False),
            (0.0, False),
        ],
    )
    def test_default_threshold_is_half(self, score, expected):
        assert determine_pass_fail(score) is expected
