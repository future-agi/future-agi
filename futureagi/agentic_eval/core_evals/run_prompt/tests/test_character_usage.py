"""Character pricing and usage must stay in the same units through formatting."""

from types import SimpleNamespace

import pytest
from litellm import ModelResponse

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter import (
    ResponseFormatter,
)

PRICING_LOOKUP = "agentic_eval.core_evals.run_prompt.model_pricing.get_model_pricing"


@pytest.mark.parametrize(
    "counts", [{"input_characters": 1000}, {"output_characters": 1000}]
)
def test_partial_character_usage_is_not_reported_as_catalog_cost(counts):
    result = calculate_total_cost("gemini-1.5-pro", counts)
    assert result["pricing_source"] == "default"


@pytest.mark.parametrize("rate", ["N/A", None, True, -1, float("inf"), float("nan")])
def test_partial_placeholder_rates_use_fallback(monkeypatch, rate):
    monkeypatch.setattr(
        PRICING_LOOKUP,
        lambda _: {"input_per_1k_characters": 0.007, "output_per_1k_characters": rate},
    )
    result = calculate_total_cost(
        "character-model", {"input_characters": 1000, "output_characters": 1000}
    )
    assert result["pricing_source"] == "default"
    assert result["total_cost"] == 0.015


@pytest.mark.parametrize(
    "pricing",
    [
        {"input_per_1M_tokens": 1, "output_per_1M_tokens": "N/A"},
        {"input_per_minute": "N/A", "other": 1},
        {"input_per_1M_characters": "N/A", "other": 1},
        {"low_quality_per_image": 0.01, "medium_quality_per_image": "N/A"},
    ],
)
def test_mixed_placeholder_units_use_labelled_fallback(monkeypatch, pricing):
    monkeypatch.setattr(PRICING_LOOKUP, lambda _: pricing)
    result = calculate_total_cost(
        "placeholder-model", {"prompt_tokens": 1000, "completion_tokens": 500}
    )
    assert result["pricing_source"] == "default"
    assert result["total_cost"] == 0.00045


def test_unusable_custom_fallback_uses_default_estimate(monkeypatch):
    monkeypatch.setattr(PRICING_LOOKUP, lambda _: None)
    result = calculate_total_cost(
        "unknown-model",
        {"prompt_tokens": 1000, "completion_tokens": 500},
        fallback_pricing={"per_request": 5},
    )
    assert result["pricing_source"] == "default"
    assert result["total_cost"] == 0.00045


@pytest.mark.parametrize(
    "characters",
    [
        {"input_characters": 50000, "output_characters": 10000},
        {"input_characters": 0, "output_characters": 0},
    ],
)
def test_response_formatter_preserves_real_character_counts(characters):
    response = SimpleNamespace(
        model="gemini-1.5-pro",
        usage=SimpleNamespace(
            prompt_tokens=123, completion_tokens=45, total_tokens=168, **characters
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content="Synthetic reply"))],
    )
    text, value_info = ResponseFormatter.format_llm_response(
        response, response.model, 0
    )
    assert text == "Synthetic reply"
    metadata = value_info["metadata"]
    assert metadata["usage"]["input_characters"] == characters["input_characters"]
    assert metadata["usage"]["output_characters"] == characters["output_characters"]
    assert metadata["cost"]["pricing_source"] == "available_models"
    assert metadata["cost"]["total_cost"] == round(
        sum(characters.values()) / 1000 * 0.007, 6
    )


@pytest.mark.parametrize(
    "invalid", ["not-a-count", -2, True, float("inf"), float("nan")]
)
@pytest.mark.parametrize("side", ["input", "output"])
def test_response_formatter_omits_invalid_character_count(side, invalid):
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "input_characters": 1000,
        "output_characters": 50,
        f"{side}_characters": invalid,
    }
    response = ModelResponse(
        model="gemini-1.5-pro",
        choices=[{"message": {"role": "assistant", "content": "Synthetic reply"}}],
        usage=usage,
    )
    reply, info = ResponseFormatter.format_llm_response(response, "gemini-1.5-pro", 0)
    assert reply == "Synthetic reply"
    metadata = info["metadata"]
    assert metadata["usage"] == {
        k: v for k, v in usage.items() if k != f"{side}_characters"
    }
    assert metadata["cost"]["pricing_source"] == "default"
    assert metadata["cost"]["total_cost"] == (0.000210 if side == "input" else 0.015)


@pytest.mark.parametrize(
    "invalid", ["not-a-count", -2, True, float("inf"), float("nan")]
)
def test_raw_invalid_input_characters_use_token_estimate(invalid):
    result = calculate_total_cost(
        "gemini-1.5-pro",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "input_characters": invalid,
            "output_characters": 50,
        },
    )
    assert result["pricing_source"] == "default"
    assert result["total_cost"] == 0.000210


@pytest.mark.parametrize("path", ["calculator", "formatter"])
@pytest.mark.parametrize(
    ("input_usage", "amount", "source"),
    [
        ({"input_characters": "not-a-count"}, 0.000210, "default"),
        ({}, 0.000210, "default"),
        ({"input_characters": 0}, 0, "available_models"),
        ({"input_characters": 1000}, 0.015, "available_models"),
    ],
)
def test_tts_cost_requires_billable_input_characters(path, input_usage, amount, source):
    model = "openai/tts-1"
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "output_characters": 50,
        **input_usage,
    }
    if path == "calculator":
        cost = calculate_total_cost(model, usage)
    else:
        response = ModelResponse(
            model=model,
            choices=[{"message": {"role": "assistant", "content": "Synthetic reply"}}],
            usage=usage,
        )
        reply, info = ResponseFormatter.format_llm_response(response, model, 0)
        assert reply == "Synthetic reply"
        cost = info["metadata"]["cost"]
    assert (cost["total_cost"], cost["pricing_source"]) == (amount, source)


def test_response_formatter_token_only_usage_is_labelled_estimate():
    response = SimpleNamespace(
        model="gemini-1.5-pro",
        usage=SimpleNamespace(
            prompt_tokens=10000, completion_tokens=2000, total_tokens=12000
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content="Synthetic reply"))],
    )
    _, value_info = ResponseFormatter.format_llm_response(response, response.model, 0)
    metadata = value_info["metadata"]
    assert "input_characters" not in metadata["usage"]
    assert "output_characters" not in metadata["usage"]
    assert metadata["cost"]["pricing_source"] == "default"
    assert metadata["cost"]["total_cost"] == 0.0027
