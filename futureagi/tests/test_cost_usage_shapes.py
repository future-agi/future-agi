"""``calculate_total_cost`` reads a usage by its values, whichever shape it arrives in.

Three rules, each from the review of the Python pricing change (TH-9538):

* a custom price with a free input and a paid output is a price;
* token usage that no table prices is billed at the token default, whatever
  speech or image keys the usage carries (log ingestion always sends them, often 0);
* a cache read is one count, whichever way the provider reports it, and it is
  priced at the cached rate in the totals and in the dated buckets alike.

No database and no services: tables are module data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_utils.token_count_helper import (
    DEFAULT_FALLBACK_PRICING,
    calculate_total_cost,
)
from agentic_eval.core_evals.run_prompt import model_pricing

ONE_MILLION_EACH = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
UNPRICED = "no-such-model-in-any-table"
DATED_MODEL = "gemini/gemini-3.7-flash"
BEFORE_2027 = datetime(2026, 12, 31, 23, 59, 0, tzinfo=UTC)
AFTER_2027 = datetime(2027, 1, 1, 0, 0, 5, tzinfo=UTC)
# gemini-3.7-flash: 0.75 in, 0.075 cached, 3.75 out until 2027, then 1.5, 0.15, 7.5.
IN_2026 = {"input": 0.75, "cached": 0.075}
IN_2027 = {"input": 1.5, "cached": 0.15}


def _cost(model, usage, **kwargs):
    return calculate_total_cost(model, dict(usage), **kwargs)


# --- A custom price with a free input and a paid output


def test_a_custom_price_with_a_free_input_bills_its_output():
    # gpt-4o is 2.5/10 in the gateway's table: the caller's own price comes first.
    result = _cost(
        "gpt-4o",
        ONE_MILLION_EACH,
        fallback_pricing={"input_per_1M_tokens": 0.0, "output_per_1M_tokens": 5.0},
    )

    assert result["prompt_cost"] == 0.0
    assert result["completion_cost"] == 5.0
    assert result["total_cost"] == 5.0
    assert result["pricing_source"] == "fallback"


def test_a_custom_price_with_only_an_output_rate_bills_it():
    result = _cost(
        "gpt-4o", ONE_MILLION_EACH, fallback_pricing={"output_per_1M_tokens": 5.0}
    )

    assert (result["prompt_cost"], result["completion_cost"]) == (0.0, 5.0)
    assert result["pricing_source"] == "fallback"


def test_a_custom_price_of_zero_and_zero_is_still_no_price():
    # A custom model that sets no price arrives as 0.0/0.0: the tables bill it.
    result = _cost(
        "gpt-4o",
        ONE_MILLION_EACH,
        fallback_pricing={"input_per_1M_tokens": 0.0, "output_per_1M_tokens": 0.0},
    )

    assert result["total_cost"] == 12.5
    assert result["pricing_source"] == "gateway_table"


def test_a_custom_price_with_an_input_rate_is_unchanged():
    result = _cost(
        "gpt-4o",
        ONE_MILLION_EACH,
        fallback_pricing={"input_per_1M_tokens": 1.0, "output_per_1M_tokens": 2.0},
    )

    assert (result["prompt_cost"], result["completion_cost"]) == (1.0, 2.0)


# --- Token usage that no table prices


TOKEN_DEFAULT = round(
    DEFAULT_FALLBACK_PRICING["input_per_1M_tokens"]
    + DEFAULT_FALLBACK_PRICING["output_per_1M_tokens"],
    6,
)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({}, id="bare"),
        pytest.param({"input_characters": 0, "audio_seconds": 0}, id="log-ingestion"),
        pytest.param({"input_characters": 0}, id="zero-characters"),
        pytest.param({"audio_seconds": 0.0}, id="zero-audio"),
        pytest.param({"images_generated": 0}, id="zero-images"),
        pytest.param(
            {"input_characters": None, "audio_seconds": None}, id="null-speech-keys"
        ),
    ],
)
def test_unpriced_token_usage_bills_the_token_default_whatever_zero_keys_it_carries(
    extra,
):
    result = _cost(UNPRICED, {**ONE_MILLION_EACH, **extra})

    assert result["total_cost"] == TOKEN_DEFAULT
    assert result["pricing_source"] == "default"


def test_the_lookup_and_the_default_classify_a_usage_alike():
    # A priced model with the same zero keys bills its table price (the lookup),
    # and an unpriced one bills the token default (the default): one reading.
    zeros = {"input_characters": 0, "audio_seconds": 0}

    assert _cost("gpt-4o", {**ONE_MILLION_EACH, **zeros})["total_cost"] == 12.5
    assert _cost(UNPRICED, {**ONE_MILLION_EACH, **zeros})["total_cost"] == TOKEN_DEFAULT


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        pytest.param({"input_characters": 1_000_000}, 15.0, id="speech"),
        pytest.param({"audio_seconds": 60}, 0.006, id="transcription"),
        pytest.param({"audio_seconds": 30}, 0.003, id="half-a-minute"),
        pytest.param({"images_generated": 2}, 0.08, id="images"),
    ],
)
def test_unpriced_speech_audio_and_image_usage_keep_their_own_default(usage, expected):
    result = _cost(UNPRICED, usage)

    assert result["total_cost"] == expected
    assert result["pricing_source"] == "default"


def test_the_token_fallback_chose_the_default_over_refusing():
    # calculate_total_cost always returns a cost dict and never raises: callers
    # read pricing_source == "default" as "no table priced this" (LLM._update_cost,
    # the log ingestion), so unpriced token usage is billed the token default and
    # flagged, not refused.
    result = _cost(UNPRICED, ONE_MILLION_EACH)

    assert set(result) == {
        "total_cost",
        "prompt_cost",
        "completion_cost",
        "pricing_source",
    }
    assert result["pricing_source"] == "default"


# --- A cache read is one count, whichever shape reports it


CACHED_800K = {
    "prompt_tokens": 1_000_000,
    "completion_tokens": 0,
}


def _at_2026(usage):
    return _cost(DATED_MODEL, usage, at=BEFORE_2027)


# 200k uncached at 0.75 and 800k cached at 0.075 per 1M.
CACHED_COST_2026 = round(0.2 * 0.75 + 0.8 * 0.075, 6)


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param({"cached_input_tokens": 800_000}, id="ours"),
        pytest.param({"cache_read_input_tokens": 800_000}, id="anthropic"),
        pytest.param(
            {"prompt_tokens_details": {"cached_tokens": 800_000}}, id="openai-chat"
        ),
        pytest.param(
            {"prompt_tokens_details": SimpleNamespace(cached_tokens=800_000)},
            id="openai-chat-object",
        ),
        pytest.param(
            {"input_tokens_details": {"cached_tokens": 800_000}}, id="openai-responses"
        ),
        pytest.param({"cached_tokens": 800_000}, id="bare"),
    ],
)
def test_every_provider_shape_of_a_cache_read_is_priced_at_the_cached_rate(shape):
    result = _at_2026({**CACHED_800K, **shape})

    assert result["prompt_cost"] == CACHED_COST_2026


def _cached_input_tokens_from(usage):
    # Looked up in the test, so a missing helper fails that test alone.
    from agentic_eval.core_evals.fi_utils import token_count_helper

    return token_count_helper.cached_input_tokens_from(usage)


def test_a_usage_that_states_the_cache_read_in_several_shapes_counts_it_once():
    # litellm carries Anthropic's count and OpenAI's for the same call.
    usage = {
        **CACHED_800K,
        "cached_input_tokens": 800_000,
        "cache_read_input_tokens": 800_000,
        "prompt_tokens_details": {"cached_tokens": 800_000},
    }

    assert _cached_input_tokens_from(usage) == 800_000
    assert _at_2026(usage)["prompt_cost"] == CACHED_COST_2026


def test_a_cache_read_larger_than_the_prompt_is_capped_at_the_prompt():
    result = _at_2026({**CACHED_800K, "cache_read_input_tokens": 5_000_000})

    assert result["prompt_cost"] == round(1.0 * 0.075, 6)


@pytest.mark.parametrize("bad", [None, -5, "many", True, 1.5e-9])
def test_a_cache_read_that_is_not_a_count_is_no_cache_read(bad):
    usage = {**CACHED_800K, "cache_read_input_tokens": bad}

    assert _cached_input_tokens_from(usage) == 0
    assert _at_2026(usage)["prompt_cost"] == round(1.0 * 0.75, 6)


def test_a_model_with_no_cached_rate_bills_the_cache_read_at_input():
    result = _cost(
        "coralbricks/glm-5.3", {**CACHED_800K, "cache_read_input_tokens": 800_000}
    )

    assert result["total_cost"] == 1.12


def test_a_usage_object_is_read_like_a_dict():
    usage = SimpleNamespace(
        prompt_tokens=1_000_000,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800_000),
    )

    assert _cached_input_tokens_from(usage) == 800_000
    assert _cached_input_tokens_from(None) == 0
    assert _cached_input_tokens_from({}) == 0


# --- The session's totals and dated buckets


@pytest.fixture
def clock(monkeypatch):
    now = {"at": BEFORE_2027}
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: now["at"])
    return now


def _session():
    with patch("agentic_eval.core.llm.llm.LLM._init_client"):
        return LLM(provider="openai", model_name=DATED_MODEL)


def _response(**usage):
    prompt = usage.pop("prompt_tokens", 1_000_000)
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=0,
            total_tokens=prompt,
            **usage,
        )
    )


def _call(llm, clock, at, response):
    clock["at"] = at
    llm._update_token_usage(response)
    llm._update_cost(response)


def _expected(rates, uncached, cached):
    return (uncached * rates["input"] + cached * rates["cached"]) / 1_000_000


SHAPES = {
    "anthropic": {"cache_read_input_tokens": 800_000},
    "openai": {"prompt_tokens_details": SimpleNamespace(cached_tokens=800_000)},
    "both": {
        "cache_read_input_tokens": 800_000,
        "prompt_tokens_details": SimpleNamespace(cached_tokens=800_000),
    },
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_wrapper_session_prices_the_cache_read_of_either_shape(clock, shape):
    llm = _session()

    _call(llm, clock, BEFORE_2027, _response(**SHAPES[shape]))

    assert llm.token_usage["cached_input_tokens"] == 800_000
    assert llm.cost["total_cost"] == pytest.approx(_expected(IN_2026, 200_000, 800_000))


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_dated_buckets_hold_the_cache_read_of_each_call(clock, shape):
    llm = _session()

    _call(llm, clock, BEFORE_2027, _response(**SHAPES[shape]))
    _call(llm, clock, AFTER_2027, _response(**SHAPES[shape]))

    # Each call at the rates in force for it, with its own cache read.
    assert llm.token_usage["cached_input_tokens"] == 1_600_000
    assert llm.cost["total_cost"] == pytest.approx(
        _expected(IN_2026, 200_000, 800_000) + _expected(IN_2027, 200_000, 800_000)
    )


def test_a_call_with_no_cache_read_adds_no_cache_keys(clock):
    llm = _session()

    _call(llm, clock, BEFORE_2027, _response())

    assert "cached_input_tokens" not in llm.token_usage
    assert llm.cost["total_cost"] == pytest.approx(_expected(IN_2026, 1_000_000, 0))
