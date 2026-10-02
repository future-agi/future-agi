"""Tests for AgentEvaluator._build_result output-type validation.

Verifies that when the model returns a value that doesn't match the
configured output_type, _build_result raises a user-facing ValueError
instead of silently producing a wrong result.

To run:
    docker exec backend bash -c "export DJANGO_SETTINGS_MODULE=tfc.settings.test && python -m pytest ee/tests/agentic_eval/tests/test_build_result_validation.py -v -s -n 0"
"""

import json

import pytest
from unittest.mock import patch, MagicMock

from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator


EVAL_FAILED_MSG = (
    "Evaluation failed. Please try again by updating your "
    "eval prompt or the input parameters."
)


def _make_evaluator(output_type="Pass/Fail", choices=None, choice_scores=None):
    """Build a minimal AgentEvaluator with mocked ModelConfigs."""
    with patch(
        "ee.evals.llm.agent_evaluator.evaluator.ModelConfigs"
    ) as mock_mc:
        mock_mc.get_config.return_value = {}
        mock_mc.is_turing.return_value = False
        ev = AgentEvaluator(
            rule_prompt="test prompt",
            model="test-model",
            output_type=output_type,
            choices=choices,
            choice_scores=choice_scores,
        )
    # _build_result reads _current_eval_id for logging
    ev._current_eval_id = "test-eval-id"
    return ev


def _agent_result(result_value, explanation="test explanation"):
    """Build a minimal agent_result dict whose content is valid JSON."""
    return {
        "content": json.dumps(
            {"result": result_value, "explanation": explanation}
        ),
    }


# ── Pass/Fail output type ───────────────────────────────────────────


class TestPassFailValidation:
    def test_pass_is_accepted(self):
        ev = _make_evaluator(output_type="Pass/Fail")
        result = ev._build_result(_agent_result("Pass"), runtime_ms=100)
        assert result["data"]["result"] == "Pass"

    def test_fail_is_accepted(self):
        ev = _make_evaluator(output_type="Pass/Fail")
        result = ev._build_result(_agent_result("Fail"), runtime_ms=100)
        assert result["data"]["result"] == "Fail"

    def test_case_insensitive(self):
        ev = _make_evaluator(output_type="Pass/Fail")
        result = ev._build_result(_agent_result("pass"), runtime_ms=100)
        assert result["data"]["result"] == "pass"

    def test_numeric_value_rejected(self):
        ev = _make_evaluator(output_type="Pass/Fail")
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result(0.85), runtime_ms=100)

    def test_arbitrary_string_rejected(self):
        ev = _make_evaluator(output_type="Pass/Fail")
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result("Maybe"), runtime_ms=100)


# ── Score / numeric output type ──────────────────────────────────────


class TestScoreValidation:
    def test_float_score_accepted(self):
        ev = _make_evaluator(output_type="score")
        result = ev._build_result(_agent_result(0.75), runtime_ms=100)
        assert result["data"]["result"] == 0.75

    def test_int_score_accepted(self):
        ev = _make_evaluator(output_type="score")
        result = ev._build_result(_agent_result(1), runtime_ms=100)
        assert result["data"]["result"] == 1

    def test_string_number_accepted(self):
        ev = _make_evaluator(output_type="score")
        result = ev._build_result(_agent_result("0.5"), runtime_ms=100)
        assert result["data"]["result"] == "0.5"

    def test_non_numeric_rejected(self):
        ev = _make_evaluator(output_type="score")
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result("Fail"), runtime_ms=100)

    def test_numeric_output_type_alias(self):
        ev = _make_evaluator(output_type="numeric")
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result("Pass"), runtime_ms=100)


# ── Choices output type ──────────────────────────────────────────────


class TestChoicesValidation:
    def test_valid_choice_accepted(self):
        ev = _make_evaluator(
            output_type="choices", choices=["Good", "Bad", "Neutral"]
        )
        result = ev._build_result(_agent_result("Good"), runtime_ms=100)
        assert result["data"]["result"] == "Good"

    def test_case_insensitive_match(self):
        ev = _make_evaluator(
            output_type="choices", choices=["Good", "Bad"]
        )
        result = ev._build_result(_agent_result("good"), runtime_ms=100)
        assert result["data"]["result"] == "good"

    def test_invalid_choice_rejected(self):
        ev = _make_evaluator(
            output_type="choices", choices=["Good", "Bad"]
        )
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result("Excellent"), runtime_ms=100)

    def test_numeric_for_choices_rejected(self):
        ev = _make_evaluator(
            output_type="choices", choices=["Good", "Bad"]
        )
        with pytest.raises(ValueError, match="Evaluation failed"):
            ev._build_result(_agent_result(0.9), runtime_ms=100)


# ── Error message content ────────────────────────────────────────────


class TestErrorMessage:
    def test_message_is_user_facing(self):
        """The error must NOT leak internal details like output_type or
        the raw model response — it should be a clean user-facing message."""
        ev = _make_evaluator(output_type="score")
        with pytest.raises(ValueError) as exc_info:
            ev._build_result(_agent_result("Fail"), runtime_ms=100)
        msg = str(exc_info.value)
        assert msg == EVAL_FAILED_MSG
        # Must not contain internal details
        assert "score" not in msg.lower() or "score" in EVAL_FAILED_MSG.lower()
        assert "model returned" not in msg.lower()
