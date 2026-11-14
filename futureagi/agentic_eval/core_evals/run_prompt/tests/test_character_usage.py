"""Character pricing and usage must stay in the same units through formatting."""

from types import SimpleNamespace

import pytest

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
        characters["input_characters"] / 1000 * 0.0003125
        + characters["output_characters"] / 1000 * 0.00125,
        6,
    )


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
