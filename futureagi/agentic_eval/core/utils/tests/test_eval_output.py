"""Tests for response_format_schema + compute_choices_failure."""

import pytest

from agentic_eval.core.utils.llm_payloads import (
    choices_judge_instructions,
    compute_choices_failure,
    is_valid_choices_result,
    response_format_schema,
)


def _envelope(result_schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "eval_result",
            "schema": {
                "type": "object",
                "properties": {
                    "result": result_schema,
                    "explanation": {"type": "string"},
                },
                "required": ["result", "explanation"],
            },
        },
    }


class TestResponseFormatSchema:
    def test_score_returns_number_schema(self):
        assert response_format_schema("score") == _envelope({"type": "number"})

    def test_numeric_returns_number_schema(self):
        assert response_format_schema("numeric") == _envelope({"type": "number"})

    def test_pass_fail_returns_enum_string(self):
        assert response_format_schema("Pass/Fail") == _envelope(
            {"type": "string", "enum": ["Pass", "Fail"]}
        )

    def test_choices_returns_enum_with_choices(self):
        assert response_format_schema("choices", ["High", "Medium", "Low"]) == _envelope(
            {"type": "string", "enum": ["High", "Medium", "Low"]}
        )

    def test_choices_multi_returns_array_schema(self):
        # OpenAI strict mode rejects minItems/uniqueItems; helper deliberately omits them.
        assert response_format_schema(
            "choices", ["A", "B", "C"], multi_choice=True
        ) == _envelope(
            {
                "type": "array",
                "items": {"type": "string", "enum": ["A", "B", "C"]},
            }
        )

    def test_multi_choice_flag_ignored_for_non_choices_output(self):
        assert response_format_schema("score", multi_choice=True) == _envelope(
            {"type": "number"}
        )
        assert response_format_schema("Pass/Fail", multi_choice=True) == _envelope(
            {"type": "string", "enum": ["Pass", "Fail"]}
        )

    def test_choices_with_empty_list_falls_back_to_string(self):
        assert response_format_schema("choices", []) == _envelope({"type": "string"})
        assert response_format_schema("choices", [], multi_choice=True) == _envelope(
            {"type": "string"}
        )

    def test_choices_with_none_falls_back_to_string(self):
        assert response_format_schema("choices", None) == _envelope({"type": "string"})

    def test_unknown_output_type_falls_back_to_string(self):
        assert response_format_schema("reason") == _envelope({"type": "string"})
        assert response_format_schema("") == _envelope({"type": "string"})
        assert response_format_schema("garbage", ["X"]) == _envelope({"type": "string"})

    def test_choices_list_is_copied_not_aliased(self):
        choices = ["A", "B"]
        schema = response_format_schema("choices", choices)
        choices.append("C")
        assert schema["json_schema"]["schema"]["properties"]["result"]["enum"] == ["A", "B"]

    def test_choices_list_is_copied_for_multi_too(self):
        choices = ["A", "B"]
        schema = response_format_schema("choices", choices, multi_choice=True)
        choices.append("C")
        items = schema["json_schema"]["schema"]["properties"]["result"]["items"]
        assert items["enum"] == ["A", "B"]


class TestEnvelopeStructure:
    @pytest.mark.parametrize(
        "output_type,choices,multi_choice",
        [
            ("score", None, False),
            ("numeric", None, False),
            ("Pass/Fail", None, False),
            ("choices", ["A"], False),
            ("choices", ["A", "B"], True),
            ("anything_else", None, False),
        ],
    )
    def test_envelope_keys_invariant(self, output_type, choices, multi_choice):
        schema = response_format_schema(output_type, choices, multi_choice=multi_choice)
        assert schema["type"] == "json_schema"
        assert schema["json_schema"]["name"] == "eval_result"
        inner = schema["json_schema"]["schema"]
        assert inner["type"] == "object"
        assert set(inner["properties"].keys()) == {"result", "explanation"}
        assert inner["properties"]["explanation"] == {"type": "string"}
        assert inner["required"] == ["result", "explanation"]


class TestComputeChoicesFailure:
    CHOICES = ["Passed", "Failed"]
    SCORES = {"Passed": 1.0, "Failed": 0.0}

    def test_single_with_scores_passes_above_threshold(self):
        assert compute_choices_failure("Passed", self.CHOICES, self.SCORES, 0.5) is False

    def test_single_with_scores_fails_below_threshold(self):
        assert compute_choices_failure("Failed", self.CHOICES, self.SCORES, 0.5) is True

    def test_single_with_scores_unknown_label_fails(self):
        assert compute_choices_failure("Other", self.CHOICES, self.SCORES, 0.5) is True

    def test_single_with_scores_case_insensitive(self):
        assert compute_choices_failure("passed", self.CHOICES, self.SCORES, 0.5) is False

    def test_single_with_scores_invalid_score_value_fails(self):
        scores = {"Passed": "not_a_number"}
        assert compute_choices_failure("Passed", self.CHOICES, scores, 0.5) is True

    def test_single_no_scores_ordinal_first_passes(self):
        assert compute_choices_failure("Passed", self.CHOICES, None, 0.5) is False

    def test_single_no_scores_ordinal_other_fails(self):
        assert compute_choices_failure("Failed", self.CHOICES, None, 0.5) is True

    def test_multi_with_scores_mean_above_threshold(self):
        result = compute_choices_failure(
            ["Passed", "Passed"], self.CHOICES, self.SCORES, 0.5, multi_choice=True
        )
        assert result is False

    def test_multi_with_scores_mean_below_threshold(self):
        result = compute_choices_failure(
            ["Passed", "Failed", "Failed"], self.CHOICES, self.SCORES, 0.5, multi_choice=True
        )
        assert result is True  # (1+0+0)/3 = 0.333 < 0.5

    def test_multi_with_scores_unknown_labels_skipped(self):
        result = compute_choices_failure(
            ["Passed", "Other"], self.CHOICES, self.SCORES, 0.5, multi_choice=True
        )
        assert result is False  # only Passed=1.0 counts → 1.0 >= 0.5

    def test_multi_with_scores_all_unknown_fails_safe(self):
        result = compute_choices_failure(
            ["Other", "Foo"], self.CHOICES, self.SCORES, 0.5, multi_choice=True
        )
        assert result is True

    def test_multi_no_scores_all_first_passes(self):
        result = compute_choices_failure(
            ["Passed", "Passed"], self.CHOICES, None, 0.5, multi_choice=True
        )
        assert result is False

    def test_multi_no_scores_any_non_first_fails(self):
        result = compute_choices_failure(
            ["Passed", "Failed"], self.CHOICES, None, 0.5, multi_choice=True
        )
        assert result is True

    def test_multi_flag_with_string_value_falls_back_to_single(self):
        # multi_choice=True but result is a string — treated as single-choice
        assert compute_choices_failure(
            "Passed", self.CHOICES, self.SCORES, 0.5, multi_choice=True
        ) is False


class TestIsValidChoicesResult:
    CHOICES = ["Love", "Anger", "Sadness", "Neutral"]

    def test_single_valid(self):
        assert is_valid_choices_result("Love", self.CHOICES) is True

    def test_single_case_insensitive(self):
        assert is_valid_choices_result("love", self.CHOICES) is True

    def test_single_invalid(self):
        assert is_valid_choices_result("Joy", self.CHOICES) is False

    def test_multi_all_valid(self):
        assert is_valid_choices_result(
            ["Love", "Sadness"], self.CHOICES, multi_choice=True
        ) is True

    def test_multi_one_invalid(self):
        assert is_valid_choices_result(
            ["Love", "Joy"], self.CHOICES, multi_choice=True
        ) is False

    def test_multi_empty_list_invalid(self):
        assert is_valid_choices_result([], self.CHOICES, multi_choice=True) is False

    def test_multi_flag_with_string_falls_back_to_single(self):
        assert is_valid_choices_result(
            "Love", self.CHOICES, multi_choice=True
        ) is True


class TestJudgeInstructions:
    def test_choices_single_lists_each_choice(self):
        text = choices_judge_instructions(["A", "B", "C"], multi_choice=False)
        assert "EXACTLY ONE" in text
        assert "'A'" in text and "'B'" in text and "'C'" in text
        assert "ARRAY" not in text

    def test_choices_multi_says_array(self):
        text = choices_judge_instructions(["A", "B"], multi_choice=True)
        assert "ONE OR MORE" in text
        assert "ARRAY" in text
        assert "'A'" in text and "'B'" in text

    def test_choices_includes_score_hint_when_passed(self):
        hint = "\nScore mapping: 'A' = 1.0, 'B' = 0.0\n"
        text = choices_judge_instructions(["A", "B"], score_hint=hint)
        assert "Score mapping" in text

    def test_choices_omits_score_hint_when_empty(self):
        text = choices_judge_instructions(["A", "B"])
        assert "Score mapping" not in text
