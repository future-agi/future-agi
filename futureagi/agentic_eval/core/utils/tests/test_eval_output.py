"""Golden tests for ``response_format_schema``.

Locks down the exact dict structure each output_type produces so any
unintentional drift (whitespace in nested keys, reordered enum, missing
``required`` etc.) breaks the build.
"""

import pytest

from agentic_eval.core.utils.eval_output import response_format_schema


def _envelope(result_schema: dict) -> dict:
    """The fixed json_schema envelope wrapping the per-type result schema."""
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
        # uniqueItems is intentionally omitted for broader provider
        # compatibility. Duplicate-handling is enforced downstream by
        # aggregate_choice_scores instead.
        assert response_format_schema(
            "choices", ["A", "B", "C"], multi_choice=True
        ) == _envelope(
            {
                "type": "array",
                "items": {"type": "string", "enum": ["A", "B", "C"]},
                "minItems": 1,
            }
        )

    def test_multi_choice_flag_ignored_for_non_choices_output(self):
        # multi_choice has no effect outside the choices branch.
        assert response_format_schema("score", multi_choice=True) == _envelope(
            {"type": "number"}
        )
        assert response_format_schema("Pass/Fail", multi_choice=True) == _envelope(
            {"type": "string", "enum": ["Pass", "Fail"]}
        )

    def test_choices_with_empty_list_falls_back_to_string(self):
        # Empty choices list is falsy → falls to default string schema.
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
        # Caller's choices list must not be aliased into the returned schema;
        # mutating the caller's list must not change the schema.
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
