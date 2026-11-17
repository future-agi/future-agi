"""AgentEvaluator's Protect run bills each call alone, at the rates in force for it.

Two things are pinned, on a model with a dated price (Gemini 3.7 Flash) and a patched
clock that crosses the price change between the two calls:
  - ``self.token_usage`` is overwritten with the current call's counts, not added to,
    so ``self.cost`` prices that call alone;
  - that price is the one in force at the call's own time (2026 for the first call,
    2027 for the second).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt import model_pricing
from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

MODEL = "gemini/gemini-3.7-flash"
FIRST = {"prompt_tokens": 1_200, "completion_tokens": 300, "total_tokens": 1_500}
SECOND = {"prompt_tokens": 4_000, "completion_tokens": 50, "total_tokens": 4_050}
FIRST_AT = datetime(2026, 12, 31, 23, 59, 0, tzinfo=UTC)
SECOND_AT = datetime(2027, 1, 1, 0, 0, 5, tzinfo=UTC)


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


def test_a_second_protect_call_is_billed_alone_at_its_own_time(monkeypatch):
    clock = {"at": FIRST_AT}
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: clock["at"])
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
        assert evaluator.cost == calculate_total_cost(MODEL, FIRST, at=FIRST_AT)

        clock["at"] = SECOND_AT
        _run_protect(evaluator)

    assert evaluator.token_usage == SECOND
    assert evaluator.cost == calculate_total_cost(MODEL, SECOND, at=SECOND_AT)
    assert evaluator.cost["total_cost"] > 0
    # The two prices differ, so a call priced at the other call's time would fail.
    assert evaluator.cost != calculate_total_cost(MODEL, SECOND, at=FIRST_AT)
