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
    assert llm.cost["pricing_source"] == "gateway_table"
    # Repriced whole at the 2027 rate this would be 4.5; at the 2026 rate, 2.25.
    assert llm.cost["total_cost"] == pytest.approx(2.25, abs=0.001)


def test_a_session_with_no_price_change_costs_what_it_always_did(clock):
    llm = _session(DATED_MODEL)

    # One token in and one out, three times. Priced call by call, each call's prompt
    # cost rounds to 0.000001 and the three add to 0.000003; priced as one total it
    # is 0.000002. Only a single bucket gives the total's figure.
    for _ in range(3):
        _call(llm, clock, datetime(2026, 12, 30, 9, 0, 0, tzinfo=UTC), 1, 1)

    assert len(llm._usage_buckets) == 1
    assert llm.cost == calculate_total_cost(DATED_MODEL, llm.token_usage)
    assert llm.cost["prompt_cost"] == 0.000002


def test_a_model_with_no_dated_price_costs_what_it_always_did(clock):
    llm = _session(UNDATED_MODEL)

    _call(llm, clock, BEFORE_MIDNIGHT, 2_000_000, 200_000)
    _call(llm, clock, AFTER_MIDNIGHT, 10, 10)

    assert llm.cost == calculate_total_cost(UNDATED_MODEL, llm.token_usage)


def test_a_session_that_starts_after_a_price_change_is_billed_at_the_new_rate(clock):
    llm = _session(DATED_MODEL)

    _call(llm, clock, AFTER_MIDNIGHT, 2_000_000, 200_000)

    new = _cost(2_000_000, 200_000, _scheduled_per_1m("2027-01-01"))
    assert llm.cost["total_cost"] == round(sum(new), 6)
    assert llm.cost["total_cost"] == pytest.approx(4.5, abs=0.001)


def test_a_model_switch_with_no_price_change_costs_what_the_total_costs(clock):
    llm = _session(UNDATED_MODEL)
    on_the_day = datetime(2026, 12, 30, 9, 0, 0, tzinfo=UTC)

    # Two models, so two buckets, but the model now in use has one rate all day.
    _call(llm, clock, on_the_day, 1, 1)
    llm.model_name = DATED_MODEL
    _call(llm, clock, on_the_day, 1, 1)

    assert len(llm._usage_buckets) == 2
    # Rounded bucket by bucket the parts add to 0.00001; the total is 0.000009.
    assert llm.cost == calculate_total_cost(DATED_MODEL, llm.token_usage, at=on_the_day)
    assert llm.cost["total_cost"] == 0.000009


def test_a_call_with_no_usage_does_not_reprice_the_session(clock):
    llm = _session(DATED_MODEL)

    _call(llm, clock, BEFORE_MIDNIGHT, 2_000_000, 200_000)
    clock["at"] = AFTER_MIDNIGHT
    response = SimpleNamespace(usage=None)
    llm._update_token_usage(response)
    llm._update_cost(response)

    old = _cost(2_000_000, 200_000, _scheduled_per_1m("2026-01-01"))
    assert llm.cost["total_cost"] == round(sum(old), 6)
    assert llm.cost["total_cost"] == pytest.approx(2.25, abs=0.001)


# --- A call is priced when it starts, as the gateway prices a request

STARTS = datetime(2026, 12, 31, 23, 59, 50, tzinfo=UTC)
USAGE_TOKENS = (2_000_000, 200_000)


def _wired_session(clock, **overrides):
    """A session whose request goes to a fake litellm; the clock moves past midnight."""
    llm = _session(DATED_MODEL)
    clock["at"] = STARTS
    llm._prepare_completion_payload = lambda *a, **k: {
        "model": DATED_MODEL,
        "messages": [{"role": "user", "content": "hi"}],
    }
    llm._try_gateway_completion = lambda *a, **k: None

    async def no_gateway(*a, **k):
        return None

    llm._try_gateway_completion_async = no_gateway
    for name, value in overrides.items():
        setattr(llm, name, value)
    return llm


def _assert_priced_at_the_2026_rate(llm):
    old = _cost(*USAGE_TOKENS, _scheduled_per_1m("2026-01-01"))
    assert llm.cost["total_cost"] == round(sum(old), 6)
    # Priced at the end (after midnight) it would be 4.5.
    assert llm.cost["total_cost"] == pytest.approx(2.25, abs=0.001)


def _chunks(clock):
    """Two streamed chunks; the call ends after midnight, with the usage chunk last."""
    first = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="he"))], usage=None
    )
    last = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="y"))],
        usage=_response(*USAGE_TOKENS).usage,
    )
    yield first
    clock["at"] = AFTER_MIDNIGHT
    yield last


def test_a_call_that_ends_after_midnight_is_priced_at_its_start(clock):
    llm = _wired_session(clock)

    def slow_completion(**kwargs):
        clock["at"] = AFTER_MIDNIGHT
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=_response(*USAGE_TOKENS).usage,
        )

    with patch("agentic_eval.core.llm.llm.litellm.completion", slow_completion):
        assert llm._get_completion_content([{"role": "user", "content": "hi"}]) == "ok"

    _assert_priced_at_the_2026_rate(llm)


def test_a_stream_that_ends_after_midnight_is_priced_at_its_start(clock):
    llm = _wired_session(clock)

    with (
        patch(
            "agentic_eval.core.llm.llm.litellm.completion",
            lambda **kwargs: _chunks(clock),
        ),
        patch("agentic_eval.core.llm.llm.call_websocket"),
    ):
        text = llm._get_completion_content(
            [{"role": "user", "content": "hi"}], streaming=True
        )

    assert text == "hey"
    _assert_priced_at_the_2026_rate(llm)


def test_a_tool_call_that_ends_after_midnight_is_priced_at_its_start(clock):
    llm = _wired_session(clock)

    def slow_completion(**kwargs):
        clock["at"] = AFTER_MIDNIGHT
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=_response(*USAGE_TOKENS).usage,
        )

    with patch("agentic_eval.core.llm.llm.litellm.completion", slow_completion):
        llm._get_completion_with_tools([{"role": "user", "content": "hi"}], tools=[])

    _assert_priced_at_the_2026_rate(llm)


@pytest.mark.asyncio
async def test_an_async_call_that_ends_after_midnight_is_priced_at_its_start(clock):
    llm = _wired_session(clock)

    async def slow_completion(**kwargs):
        clock["at"] = AFTER_MIDNIGHT
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=_response(*USAGE_TOKENS).usage,
        )

    with patch("agentic_eval.core.llm.llm.litellm.acompletion", slow_completion):
        text = await llm._get_completion_content_async(
            [{"role": "user", "content": "hi"}]
        )

    assert text == "ok"
    _assert_priced_at_the_2026_rate(llm)


@pytest.mark.asyncio
async def test_an_async_stream_that_ends_after_midnight_is_priced_at_its_start(clock):
    from unittest.mock import AsyncMock

    llm = _wired_session(clock)

    async def stream(**kwargs):
        async def chunks():
            for chunk in _chunks(clock):
                yield chunk

        return chunks()

    with patch("agentic_eval.core.llm.llm.litellm.acompletion", stream):
        text = await llm._get_completion_content_async(
            [{"role": "user", "content": "hi"}],
            streaming=True,
            ws_manager=AsyncMock(),
        )

    assert text == "hey"
    _assert_priced_at_the_2026_rate(llm)
