"""The backend bills a model from the gateway's price table first.

``lookup_pricing`` tries, in order: the decision models, the gateway's table (a
byte copy of agentcc-gateway/internal/modeldb/litellm.json, read the way the
gateway reads it), then the platform catalogue (AVAILABLE_MODELS). These tests pin
which table prices which name, at which price, and the rules between them: the
shape rule (a token call needs an input price), speech, the caller's own price,
and the catalogue's name matching, which no longer lets ``-cyber`` or ``-lite``
borrow a base model's price.

No database and no services: tables are module data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt import model_pricing
from agentic_eval.core_evals.run_prompt.gateway_prices import GATEWAY_PRICES
from agentic_eval.core_evals.run_prompt.model_pricing import (
    get_model_info,
    get_model_pricing,
    lookup_pricing,
    price_change_dates,
)

NOW26 = datetime(2026, 11, 11, 12, tzinfo=UTC)
T26 = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
T27 = datetime(2027, 1, 1, tzinfo=UTC)
J2 = datetime(2027, 1, 2, tzinfo=UTC)
START_2026 = datetime(2026, 1, 1, tzinfo=UTC)
START_2027 = datetime(2027, 1, 1, tzinfo=UTC)

ONE_MILLION_EACH = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}

RATES_2026 = {
    "input_per_1M_tokens": 0.75,
    "output_per_1M_tokens": 3.75,
    "cached_input_per_1M_tokens": 0.075,
}
RATES_2027 = {
    "input_per_1M_tokens": 1.5,
    "output_per_1M_tokens": 7.5,
    "cached_input_per_1M_tokens": 0.15,
}


def _cost(model, usage=ONE_MILLION_EACH, **kwargs):
    return calculate_total_cost(model, dict(usage), **kwargs)


def _rates(model, at):
    return get_model_pricing(model, at=at)


# --- Names the gateway prices that the catalogue would price wrongly or not at all


@pytest.mark.parametrize("at", [NOW26, J2], ids=["now", "january-2"])
def test_a_suffixed_name_is_billed_at_its_own_gateway_key(at):
    result = _cost("gemini-3.8-flash-cyber", at=at)

    # gemini-3.8-flash-cyber is its own key at 1.5/7.5, undated.
    assert result["total_cost"] == 9.0
    assert result["pricing_source"] == "gateway_table"


def test_the_catalogue_does_not_resolve_a_suffix_to_the_base_model():
    assert get_model_info("gemini-3.8-flash-cyber") is None


@pytest.mark.parametrize(
    "name",
    [
        "gemini-3.7-flash-lite",
        "gemini-3.7-flash-001",
        "gemini-3.6-flash-preview",
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
    ],
)
def test_a_suffix_that_is_another_model_does_not_borrow_the_base_price(name):
    # No gateway key, and the catalogue's gate refuses the suffix: the 2027 price
    # of gemini-3.7-flash must not be billed to a different model.
    assert get_model_pricing(name, at=J2) is None


@pytest.mark.parametrize(
    "name",
    [
        "google/gemini-3.7-flash",
        "models/gemini-3.7-flash",
        "vertex_ai_global/gemini-3.7-flash",
        "publishers/google/models/gemini-3.8-flash",
    ],
)
def test_googles_own_name_forms_are_billed_and_dated(name):
    before = lookup_pricing(name, at=NOW26)
    after = lookup_pricing(name, at=J2)

    assert before.pricing == RATES_2026
    assert after.pricing == RATES_2027
    assert before.source == after.source == "gateway_table"
    assert price_change_dates(name) == (START_2026, START_2027)


def test_the_catalogue_strips_googles_prefix_too():
    assert get_model_info("google/gemini-3.7-flash")["model_name"] == (
        "gemini/gemini-3.7-flash"
    )


@pytest.mark.parametrize(
    "name", ["openrouter/google/gemini-3.7-flash", "github_copilot/gemini-3.7-flash"]
)
def test_a_resellers_own_key_is_billed_undated(name):
    found = lookup_pricing(name, at=J2)

    assert found.pricing == RATES_2026
    assert found.source == "gateway_table"
    assert price_change_dates(name) == ()


def test_a_reseller_path_is_billed_by_its_last_segment():
    name = "deepinfra/google/gemini-3.6-flash"

    assert _rates(name, T26) == RATES_2026
    assert _rates(name, T27) == RATES_2027


# --- Order and the unpriced set


def test_a_name_only_the_catalogue_prices_is_billed_from_it():
    result = _cost("codestral/codestral-latest")

    assert result["total_cost"] == 12.0
    assert result["pricing_source"] == "available_models"


def test_a_zero_priced_entry_is_not_a_price():
    # The exact key is a 0/0 entry, which the loader drops; the bare key is 1.25/10.
    result = _cost("gemini/gemini-2.5-pro-exp-03-25")

    assert result["total_cost"] == 11.25
    assert result["pricing_source"] == "gateway_table"


@pytest.mark.parametrize(
    "name",
    [
        "ollama/llama3",
        "openrouter/openrouter/auto",
        "perplexity/sonar-pro-online",
        "fireworks-ai-default",
    ],
)
def test_names_no_table_prices_are_not_billed_from_the_gateway_table(name):
    assert _cost(name)["pricing_source"] != "gateway_table"


def test_an_unpriced_name_bills_the_default():
    result = _cost("ollama/llama3")

    assert result["total_cost"] == 0.75
    assert result["pricing_source"] == "default"


def test_an_entry_with_no_mode_is_a_chat_entry():
    result = _cost("fireworks-ai-above-16b")

    assert result["total_cost"] == 1.8
    assert result["pricing_source"] == "gateway_table"


def test_an_embedding_entry_does_not_price_a_chat_call():
    # The entry found, gemini/gemini-1.5-flash, is an embedding entry; the
    # catalogue's row for the name is per 1k characters.
    result = _cost("deepinfra/gemini/gemini-1.5-flash")

    assert result["total_cost"] == 0.75
    assert result["pricing_source"] == "default"


def test_the_lookup_follows_the_gateways_rules_then_lower_case():
    result = _cost("Azure/eu/gpt-4o")

    assert result["total_cost"] == 12.5
    assert result["pricing_source"] == "gateway_table"
    assert get_model_pricing("GPT-4O") == get_model_pricing("gpt-4o")


# --- Speech, and the caller's own price


def test_speech_skips_the_gateway_table():
    speech = _cost(
        "vertex_ai/gemini-2.5-pro-preview-tts",
        {"input_characters": 1000, **ONE_MILLION_EACH},
    )
    chat = _cost("vertex_ai/gemini-2.5-pro-preview-tts")

    assert speech["total_cost"] == 6.25
    assert speech["pricing_source"] == "available_models"
    assert chat["total_cost"] == 21.0
    assert chat["pricing_source"] == "gateway_table"


def test_a_speech_model_bills_characters():
    result = _cost("gpt-4o-mini-tts", {"input_characters": 1_000_000})

    assert result["total_cost"] == 10.0
    assert result["pricing_source"] == "available_models"


def test_the_callers_own_price_comes_before_both_tables():
    result = _cost(
        "acme/gpt-4o",
        {"prompt_tokens": 1000, "completion_tokens": 500},
        fallback_pricing={"input_per_1M_tokens": 1.0, "output_per_1M_tokens": 2.0},
    )

    # The gateway table would give 0.0075; the catalogue's alias 0.0125.
    assert result["total_cost"] == 0.002
    assert result["pricing_source"] == "fallback"


@pytest.mark.parametrize(
    "fallback",
    [
        {"input_per_1M_tokens": 0.0, "output_per_1M_tokens": 0.0},
        {"output_per_1M_tokens": 2.0},
    ],
    ids=["zero-price", "no-input-price"],
)
def test_a_callers_price_with_no_input_rate_is_no_price(fallback):
    # A custom model that sets no price arrives as 0.0/0.0 and must not bill $0.
    result = _cost(
        "acme/gpt-4o",
        {"prompt_tokens": 1000, "completion_tokens": 500},
        fallback_pricing=fallback,
    )

    assert result["total_cost"] == 0.0075
    assert result["pricing_source"] == "gateway_table"


def test_a_decision_model_is_priced_before_the_gateway_table():
    assert get_model_pricing("decision/openai/gpt-6-luna") == {
        "input_per_1M_tokens": 0.1,
        "output_per_1M_tokens": 0.0,
    }
    assert get_model_pricing("gpt-6-luna") == {
        "input_per_1M_tokens": 0.1,
        "output_per_1M_tokens": 0.5,
        "cached_input_per_1M_tokens": 0.01,
    }


def test_an_entry_with_no_cached_rate_bills_every_input_token_at_input():
    result = _cost(
        "coralbricks/glm-5.3",
        {
            "prompt_tokens": 1_000_000,
            "cached_input_tokens": 800_000,
            "completion_tokens": 0,
        },
    )

    assert result["total_cost"] == 1.12


def test_a_per_character_row_is_no_price_for_a_token_call():
    # The catalogue's row has no input_per_1M_tokens. It must not bill $0.
    result = _cost("openrouter/google/gemini-pro-1.5")

    assert result["total_cost"] == 0.75
    assert result["pricing_source"] == "default"


def test_an_input_only_price_bills_its_input_rate():
    # An embedding row has an input price and no output price.
    pricing = get_model_pricing("text-embedding-3-small")
    result = _cost("text-embedding-3-small", {"prompt_tokens": 1_000_000})

    assert "output_per_1M_tokens" not in pricing
    assert pricing["input_per_1M_tokens"] == 0.02
    assert result["total_cost"] == 0.02
    assert result["completion_cost"] == 0.0
    assert result["pricing_source"] == "available_models"


# --- The table is read-only, and zero entries


def test_a_caller_cannot_change_the_table_through_a_result():
    first = lookup_pricing("gemini/gemini-3.7-flash", at=T26)
    first.pricing["input_per_1M_tokens"] = 99
    second = lookup_pricing("gemini/gemini-3.7-flash", at=J2)
    second.pricing["input_per_1M_tokens"] = 99
    third = lookup_pricing("gemini/gemini-3.7-flash", at=T26)

    assert first.pricing["input_per_1M_tokens"] == 99
    assert third.pricing == RATES_2026
    assert dict(GATEWAY_PRICES["gemini/gemini-3.7-flash"][1]) == RATES_2026


def test_a_zero_priced_entry_falls_through_to_the_default():
    name = "fireworks_ai/accounts/fireworks/models/gpt-oss-120b"
    priced = _cost(name)

    assert get_model_pricing(name)["input_per_1M_tokens"] == 0.15
    assert get_model_pricing(name)["output_per_1M_tokens"] == 0.6
    assert priced["pricing_source"] == "gateway_table"
    # The catalogue's gpt-oss-120b is 0/0: no price.
    bare = _cost("gpt-oss-120b")
    assert get_model_pricing("gpt-oss-120b") is None
    assert bare["total_cost"] == 0.75
    assert bare["pricing_source"] == "default"


# --- Bedrock ARNs


ARN = (
    "arn:aws:bedrock:us-east-1:111122223333:inference-profile/"
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)


@pytest.mark.parametrize("name", [ARN, f"bedrock/{ARN}"], ids=["arn", "bedrock-arn"])
def test_a_bedrock_arn_is_billed_by_its_model(name):
    result = _cost(name)

    assert result["total_cost"] == pytest.approx(19.8)
    assert result["pricing_source"] == "gateway_table"
    assert get_model_pricing(name) == {
        "input_per_1M_tokens": 3.3,
        "output_per_1M_tokens": 16.5,
        "cached_input_per_1M_tokens": 0.33,
    }


@pytest.mark.requires_ee
def test_the_catalogue_resolves_an_arn_to_the_row_the_platform_builds():
    from ee.prompts.additional_models import _bedrock_arn

    assert get_model_info(ARN)["model_name"] == _bedrock_arn(
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )


@pytest.mark.parametrize(
    ("name", "input_rate", "output_rate"),
    [
        ("gpt-4o-mini", 0.15, 0.6),
        ("us.anthropic.claude-sonnet-4-6", 3.3, 16.5),
        ("gpt-4", 30.0, 60.0),
        ("groq/llama-3.3-70b-versatile", 0.59, 0.79),
    ],
)
def test_the_platforms_defaults_are_billed_at_the_gateways_prices(
    name, input_rate, output_rate
):
    pricing = get_model_pricing(name)

    assert pricing["input_per_1M_tokens"] == input_rate
    assert pricing["output_per_1M_tokens"] == output_rate


# --- The catalogue's matching gates (the step-4 gate, and the Google forms)


def test_a_prefixed_name_does_not_expand_to_a_different_model():
    # Step 4 of the catalogue match lets the catalogue name be more specific than
    # the request, but only by a date or a version. These used to resolve to
    # claude-sonnet-4-5-20250929 and ollama/llama3.
    assert get_model_info("github_copilot/claude-sonnet-4") is None
    assert get_model_info("snowflake/llama3-70b") is None


def test_a_dated_catalogue_row_is_still_found_for_its_undated_name():
    assert get_model_info("claude-3-5-haiku")["model_name"] == (
        "claude-3-5-haiku-20241022"
    )
    assert get_model_info("claude-sonnet-4-5")["model_name"] == (
        "claude-sonnet-4-5-20250929"
    )


@pytest.mark.parametrize(
    ("name", "row"),
    [
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5-20250929"),
        ("claude-3-5-haiku-20241022-v1:0", "claude-3-5-haiku-20241022"),
        ("gemini-3.7-flash@default", "gemini/gemini-3.7-flash"),
        ("gpt-4o-latest", "gpt-4o"),
    ],
)
def test_a_request_more_specific_by_a_date_or_version_still_matches(name, row):
    # Step 3: the request carries a date, a version or "-latest" past the
    # catalogue's name.
    assert get_model_info(name)["model_name"] == row


def test_the_catalogues_dated_layer_prices_a_name_the_copy_lacks():
    name = "vertex_ai/gemini-3.6-flash@default"
    assert name not in GATEWAY_PRICES
    assert model_pricing.gateway_token_rates(name) is None

    found = lookup_pricing(name, at=J2)

    assert found.pricing == RATES_2027
    assert found.source == "available_models"
    assert found.key == "vertex_ai/gemini-3.6-flash"


@pytest.mark.parametrize(
    ("name", "usage", "expected"),
    [
        # The copy's entries for these are chat entries; an image or audio call is
        # not billed by tokens, so they don't price it.
        (
            "fireworks_ai/accounts/fireworks/models/flux-1-dev",
            {"prompt_tokens": 0, "completion_tokens": 0, "images_generated": 1},
            0.04,
        ),
        ("amazon-nova/nova-lite-v1", {"audio_seconds": 600}, 0.06),
    ],
    ids=["image", "audio"],
)
def test_an_image_or_audio_call_skips_the_gateway_table(name, usage, expected):
    result = _cost(name, usage)

    assert result["total_cost"] == expected
    assert result["pricing_source"] == "default"


def test_a_callers_price_by_characters_is_used_as_given():
    result = _cost(
        "acme-tts",
        {"input_characters": 1000},
        fallback_pricing={"input_per_1M_characters": 30.0},
    )

    assert result["total_cost"] == 0.03
    assert result["pricing_source"] == "fallback"


def test_a_usage_time_without_a_time_zone_is_refused_even_with_a_callers_price():
    with pytest.raises(ValueError):
        calculate_total_cost(
            "acme/gpt-4o",
            {"prompt_tokens": 1000, "completion_tokens": 500},
            {"input_per_1M_tokens": 1.0, "output_per_1M_tokens": 2.0},
            at=datetime(2026, 11, 11, 12),
        )
