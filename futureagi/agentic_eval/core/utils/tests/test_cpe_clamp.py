"""Integration tests for the score clamp wiring inside CustomPromptEvaluator._evaluate.

These mirror the AgentEvaluator clamp tests in
`ee/evals/llm/agent_evaluator/tests/test_evaluator_failure_paths.py`.

Lives under `agentic_eval/core/utils/tests/` (not the CPE tests dir) because
CPE's own tests dir does not currently collect under the project pytest config.
"""

from unittest.mock import patch

import pytest
from jinja2 import Environment
from structlog.testing import capture_logs

from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined


def _make_cpe(output_type, choices=None, choice_scores=None):
    """Construct a CPE without running LLM.__init__."""
    ev = CustomPromptEvaluator.__new__(CustomPromptEvaluator)
    ev.rule_prompt = "evaluate the input"
    ev.system_prompt = None
    ev._output_type = output_type
    ev._model = "stub-model"
    ev._choices = choices or []
    ev._multi_choice = False
    ev._choice_scores = choice_scores
    ev._few_shot_examples = None
    ev._messages = None
    ev.provider = "openai"
    ev._is_turing = False
    ev.system_template_value = ""
    ev.temperature = 0.0
    ev.max_tokens = 256
    ev.knowledge_base_id = None
    ev.check_internet = False
    ev.template_format = "mustache"
    ev.token_usage = {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}
    ev.cost = {"total_cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0}
    ev.env = Environment(
        variable_start_string="{{",
        variable_end_string="}}",
        undefined=PreserveUndefined,
    )
    return ev


def _patched_evaluate(ev, llm_response_json: str):
    """Run ev._evaluate with the LLM call patched to return llm_response_json."""
    with patch.object(ev, "call_llm", return_value=llm_response_json):
        return ev._evaluate()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.5, 0.5),
        (3.5, 1.0),
        (-0.1, 0.0),
        (7, 1.0),
    ],
)
def test_cpe_evaluate_clamps_score(raw, expected):
    ev = _make_cpe(output_type="score")
    result = _patched_evaluate(ev, f'{{"result": {raw}, "explanation": "x"}}')
    assert result["data"]["result"] == expected


def test_cpe_evaluate_clamps_numeric():
    ev = _make_cpe(output_type="numeric")
    result = _patched_evaluate(ev, '{"result": 4.2, "explanation": "x"}')
    assert result["data"]["result"] == 1.0


@pytest.mark.parametrize("label", ["Pass", "Fail"])
def test_cpe_evaluate_passfail_not_clamped(label):
    ev = _make_cpe(output_type="Pass/Fail")
    result = _patched_evaluate(ev, f'{{"result": "{label}", "explanation": "x"}}')
    assert result["data"]["result"] == label


@pytest.mark.parametrize("label", ["1", "10", "joy"])
def test_cpe_evaluate_choices_labels_not_clamped(label):
    ev = _make_cpe(output_type="choices", choices=["1", "10", "joy"])
    result = _patched_evaluate(ev, f'{{"result": "{label}", "explanation": "x"}}')
    assert result["data"]["result"] == label


def test_cpe_out_of_range_emits_warning():
    ev = _make_cpe(output_type="score")
    with capture_logs() as captured:
        _patched_evaluate(ev, '{"result": 3.5, "explanation": "x"}')
    events = [e["event"] for e in captured]
    assert "eval_score_out_of_range_clamped" in events
