"""Cost provenance survives LLM accumulation without inventing charges."""

from types import SimpleNamespace

import pytest

from agentic_eval.core.llm.llm import LLM


def make_llm():
    llm = LLM.__new__(LLM)
    llm.model_name = "unknown-test-model"
    llm.token_usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    llm.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
    return llm


@pytest.mark.parametrize("amount", [0, 0.25])
def test_litellm_cost_retains_source_and_zero(amount):
    llm = make_llm()
    llm._update_cost(SimpleNamespace(_hidden_params={"response_cost": amount}))
    assert llm.cost["total_cost"] == amount
    assert llm.cost["pricing_source"] == "litellm"


@pytest.mark.parametrize("first_reported", [True, False])
def test_mixed_estimates_price_each_request_once(first_reported):
    llm = make_llm()
    reported = SimpleNamespace(_hidden_params={"response_cost": 0.25})
    llm._update_cost(reported if first_reported else None)
    llm.token_usage = {"prompt_tokens": 2000, "completion_tokens": 1000}
    llm._update_cost(None if first_reported else reported)
    assert llm.cost["total_cost"] == pytest.approx(0.25045)
    assert llm.cost["pricing_source"] == "mixed_estimate"
