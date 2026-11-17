"""AgentEvaluator's Protect run bills each call on its own.

``self.token_usage`` is overwritten with the current call's counts, not added to, so
``self.cost`` prices that call alone. The evaluator is built per eval and read once
per call; a price that follows the call's own time is what that needs, and the
running total the LLM class keeps does not apply here.
"""

from types import SimpleNamespace
from unittest.mock import patch

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

MODEL = "gpt-4o"
FIRST = {"prompt_tokens": 1_200, "completion_tokens": 300, "total_tokens": 1_500}
SECOND = {"prompt_tokens": 4_000, "completion_tokens": 50, "total_tokens": 4_050}


def _response(usage):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Passed"))],
        usage=SimpleNamespace(**usage),
    )


class _FakeLLM:
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    def _try_gateway_completion(self, payload):
        return self.responses.pop(0)


def _run_protect(evaluator):
    return evaluator._run_protect(
        {"eval_name": "toxicity", "input": "hello"}, "protect", 0.0
    )


def test_a_second_protect_call_is_billed_alone():
    evaluator = AgentEvaluator(
        rule_prompt="toxicity",
        model="turing_large",
        output_type="Pass/Fail",
        organization_id="org-1",
        workspace_id="ws-1",
    )
    evaluator._current_eval_id = "eval-1"
    _FakeLLM.responses = [_response(FIRST), _response(SECOND)]

    with (
        patch("agentic_eval.core.llm.llm.LLM", _FakeLLM),
        patch("ee.protect.helper.ProtectHelper.resolve_alias", return_value=MODEL),
        patch("ee.protect.helper.ProtectHelper.build_messages", return_value=[]),
        patch(
            "ee.protect.helper.ProtectHelper.parse_response",
            return_value={"choices": ["Passed"], "explanation": ""},
        ),
    ):
        _run_protect(evaluator)
        assert evaluator.token_usage == FIRST
        assert evaluator.cost == calculate_total_cost(MODEL, FIRST)

        _run_protect(evaluator)

    assert evaluator.token_usage == SECOND
    assert evaluator.cost == calculate_total_cost(MODEL, SECOND)
    assert evaluator.cost["total_cost"] > 0
