"""Tests for CustomPromptEvaluator._build_response_format().

Verifies that the json_schema is correctly typed for each eval output type.
"""

import pytest


@pytest.fixture
def make_evaluator():
    """Create a minimal CustomPromptEvaluator without LLM init."""
    from unittest.mock import patch

    def _make(output_type="score", choices=None):
        # Patch LLM.__init__ to avoid provider/model setup
        with patch.object(
            __import__(
                "agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator",
                fromlist=["CustomPromptEvaluator"],
            ).CustomPromptEvaluator,
            "__init__",
            lambda self, **kw: None,
        ):
            from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
                CustomPromptEvaluator,
            )

            ev = CustomPromptEvaluator.__new__(CustomPromptEvaluator)
            ev._output_type = output_type
            ev._choices = choices or []
            return ev

    return _make


class TestBuildResponseFormat:
    def test_score_type_returns_number_schema(self, make_evaluator):
        ev = make_evaluator(output_type="score")
        fmt = ev._build_response_format()

        assert fmt["type"] == "json_schema"
        schema = fmt["json_schema"]["schema"]
        assert schema["properties"]["result"]["type"] == "number"
        assert "explanation" in schema["properties"]
        assert schema["required"] == ["result", "explanation"]

    def test_numeric_type_returns_number_schema(self, make_evaluator):
        ev = make_evaluator(output_type="numeric")
        fmt = ev._build_response_format()

        assert fmt["json_schema"]["schema"]["properties"]["result"]["type"] == "number"

    def test_pass_fail_type_returns_enum(self, make_evaluator):
        ev = make_evaluator(output_type="Pass/Fail")
        fmt = ev._build_response_format()

        result_schema = fmt["json_schema"]["schema"]["properties"]["result"]
        assert result_schema["type"] == "string"
        assert result_schema["enum"] == ["Pass", "Fail"]

    def test_choices_type_returns_enum(self, make_evaluator):
        ev = make_evaluator(output_type="choices", choices=["Good", "Bad", "Neutral"])
        fmt = ev._build_response_format()

        result_schema = fmt["json_schema"]["schema"]["properties"]["result"]
        assert result_schema["type"] == "string"
        assert result_schema["enum"] == ["Good", "Bad", "Neutral"]

    def test_unknown_type_returns_string(self, make_evaluator):
        ev = make_evaluator(output_type="reason")
        fmt = ev._build_response_format()

        result_schema = fmt["json_schema"]["schema"]["properties"]["result"]
        assert result_schema["type"] == "string"
        assert "enum" not in result_schema

    def test_schema_has_no_additional_properties(self, make_evaluator):
        ev = make_evaluator(output_type="score")
        fmt = ev._build_response_format()

        assert "additionalProperties" not in fmt["json_schema"]["schema"]

    def test_schema_name_is_eval_result(self, make_evaluator):
        ev = make_evaluator(output_type="score")
        fmt = ev._build_response_format()

        assert fmt["json_schema"]["name"] == "eval_result"
