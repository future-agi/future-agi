"""A session's cost is priced call by call, at the rates in force for each call.

The session's token totals stay as they were; the price follows the clock of every
call, so a session that runs across a price change bills the calls made before it at
the old rate. No database: the catalogue and the dated prices are module data.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt import model_pricing
from agentic_eval.core_evals.run_prompt.price_schedule import PRICE_SCHEDULE_PATH

DATED_MODEL = "gemini/gemini-3.7-flash"
UNDATED_MODEL = "gpt-4o"
BEFORE_MIDNIGHT = datetime(2026, 12, 31, 23, 59, 0, tzinfo=UTC)
AFTER_MIDNIGHT = datetime(2027, 1, 1, 0, 0, 5, tzinfo=UTC)


def _scheduled_per_1m(date):
    """The dated file's input and output price per 1M tokens from a row's date."""
    rows = json.loads(PRICE_SCHEDULE_PATH.read_text(encoding="utf-8"))[
        "gemini-3.7-flash"
    ]
    row = next(row for row in rows if row["from"] == date)
    return row["input_cost_per_token"] * 1e6, row["output_cost_per_token"] * 1e6


def _cost(prompt_tokens, completion_tokens, rates):
    prompt = round(prompt_tokens / 1_000_000 * rates[0], 6)
    completion = round(completion_tokens / 1_000_000 * rates[1], 6)
    return prompt, completion


def _response(prompt_tokens, completion_tokens):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
    )


@pytest.fixture
def clock(monkeypatch):
    now = {"at": BEFORE_MIDNIGHT}
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: now["at"])
    return now


def _session(model_name):
    with patch("agentic_eval.core.llm.llm.LLM._init_client"):
        return LLM(provider="openai", model_name=model_name)


def _call(llm, clock, at, prompt_tokens, completion_tokens):
    clock["at"] = at
    response = _response(prompt_tokens, completion_tokens)
    llm._update_token_usage(response)
    llm._update_cost(response)


def test_calls_before_a_price_change_keep_the_old_rate(clock):
    llm = _session(DATED_MODEL)

    _call(llm, clock, BEFORE_MIDNIGHT, 2_000_000, 200_000)
    _call(llm, clock, AFTER_MIDNIGHT, 10, 10)

    old = _cost(2_000_000, 200_000, _scheduled_per_1m("2026-01-01"))
    new = _cost(10, 10, _scheduled_per_1m("2027-01-01"))
    assert llm.token_usage == {
        "total_tokens": 2_200_020,
        "prompt_tokens": 2_000_010,
        "completion_tokens": 200_010,
    }
    assert llm.cost["prompt_cost"] == round(old[0] + new[0], 6)
    assert llm.cost["completion_cost"] == round(old[1] + new[1], 6)
    assert llm.cost["total_cost"] == round(sum(old) + sum(new), 6)
    assert llm.cost["pricing_source"] == "available_models"
    # Repriced whole at the 2027 rate this would be 4.5; at the 2026 rate, 2.25.
    assert llm.cost["total_cost"] == pytest.approx(2.25, abs=0.001)


def test_a_session_with_no_price_change_costs_what_it_always_did(clock):
    llm = _session(DATED_MODEL)

    for prompt_tokens, completion_tokens in ((1_000, 200), (50_000, 7_000), (3, 4)):
        _call(
            llm,
            clock,
            datetime(2026, 12, 30, 9, 0, 0, tzinfo=UTC),
            prompt_tokens,
            completion_tokens,
        )

    assert llm.cost == calculate_total_cost(DATED_MODEL, llm.token_usage)


def test_a_model_with_no_dated_price_costs_what_it_always_did(clock):
    llm = _session(UNDATED_MODEL)

    _call(llm, clock, BEFORE_MIDNIGHT, 2_000_000, 200_000)
    _call(llm, clock, AFTER_MIDNIGHT, 10, 10)

    assert llm.cost == calculate_total_cost(UNDATED_MODEL, llm.token_usage)
