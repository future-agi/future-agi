"""An agent run with no gateway cost is priced at the rates in force when it started.

The loop reports one running total of tokens for the whole run, so the fallback prices
them at a single instant. That instant is the run's start: a run that crosses a price
change is billed at the old rate for its whole length (a bounded under-charge), never
at the new rate for tokens spent before the change. A gateway-reported cost is used as
it is.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt import model_pricing
from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

MODEL = "gemini/gemini-3.7-flash"
RUN_STARTS = datetime(2026, 12, 31, 23, 58, 0, tzinfo=UTC)
RUN_ENDS = datetime(2027, 1, 1, 0, 1, 0, tzinfo=UTC)
LATER_RUN_STARTS = datetime(2027, 1, 1, 0, 1, 0, tzinfo=UTC)
LATER_RUN_ENDS = datetime(2027, 1, 1, 0, 3, 0, tzinfo=UTC)
AGENT_RESULT = {
    "content": '{"result": "Pass", "explanation": "ok"}',
    "tool_calls": [],
    "input_tokens": 2_000_000,
    "output_tokens": 200_000,
}


def _run(monkeypatch, gateway_cost, starts=RUN_STARTS, ends=RUN_ENDS):
    clock = {"at": starts}
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: clock["at"])

    async def run(**kwargs):
        clock["at"] = ends
        return dict(AGENT_RESULT)

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    client = MagicMock()
    client.last_oversized_attempt = None
    client._gateway_cost = gateway_cost

    evaluator = AgentEvaluator(
        rule_prompt="check that the response is correct",
        model="turing_large",
        output_type="Pass/Fail",
        organization_id="org-1",
        workspace_id="ws-1",
    )
    evaluator._model = MODEL
    with (
        patch("ee.falcon_ai.agent.AgentLoop", return_value=agent),
        patch(
            "ee.evals.llm.agent_evaluator.evaluator.EvalLLMClient", return_value=client
        ),
        patch("ai_tools.registry.registry"),
        patch.object(evaluator, "_build_tool_context", return_value=MagicMock()),
    ):
        evaluator._run_agent(
            "Judge the trace.", llm_override={"provider": "bedrock", "model": "x"}
        )
    return evaluator


def test_a_run_across_a_price_change_is_billed_at_the_rate_it_started_under(
    monkeypatch,
):
    evaluator = _run(monkeypatch, gateway_cost=0)

    at_start = calculate_total_cost(MODEL, evaluator.token_usage, at=RUN_STARTS)
    at_end = calculate_total_cost(MODEL, evaluator.token_usage, at=RUN_ENDS)
    assert at_start["total_cost"] != at_end["total_cost"]
    assert evaluator.cost["total_cost"] == at_start["total_cost"]
    assert evaluator.cost["total_cost"] == 2.25


def test_a_run_that_starts_after_a_price_change_is_billed_at_the_new_rate(
    monkeypatch,
):
    evaluator = _run(
        monkeypatch, gateway_cost=0, starts=LATER_RUN_STARTS, ends=LATER_RUN_ENDS
    )

    old = calculate_total_cost(MODEL, evaluator.token_usage, at=RUN_STARTS)
    assert old["total_cost"] == 2.25
    assert evaluator.cost["total_cost"] == 4.5


def test_a_gateway_reported_cost_is_used_as_it_is(monkeypatch):
    evaluator = _run(monkeypatch, gateway_cost=1.23)

    assert evaluator.cost["total_cost"] == 1.23
    assert evaluator.cost["pricing_source"] == "gateway"
