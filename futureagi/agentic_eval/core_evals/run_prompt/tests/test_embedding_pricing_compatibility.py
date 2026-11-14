"""Input-only token and image pricing compose with existing pricing rules."""

import pytest

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost


@pytest.mark.parametrize("image_key", ["input_per_image", "image_input_per_image"])
def test_embedding_cache_discount_and_images_are_additive(image_key):
    result = calculate_total_cost(
        "synthetic-embedding-example",
        {"prompt_tokens": 1000, "cached_input_tokens": 800, "num_images": 2},
        fallback_pricing={
            "input_per_1M_tokens": 1,
            "cached_input_per_1M_tokens": 0.1,
            image_key: 0.002,
        },
    )
    assert result == {
        "prompt_cost": 0.00428,
        "completion_cost": 0.0,
        "total_cost": 0.00428,
        "pricing_source": "fallback",
    }


def test_zero_input_image_rate_does_not_use_alias():
    result = calculate_total_cost(
        "synthetic-embedding-example",
        {"num_images": 2},
        fallback_pricing={"input_per_image": 0, "image_input_per_image": 0.002},
    )
    assert result["total_cost"] == 0


def test_explicit_zero_input_images_does_not_use_output_count():
    result = calculate_total_cost(
        "amazon.titan-embed-image-v1",
        {"num_images": 0, "images_generated": 3, "prompt_tokens": 1000},
    )
    assert result["total_cost"] == 0.0008


@pytest.mark.parametrize("rate", ["N/A", None, -1, float("nan"), float("inf"), True])
def test_invalid_input_image_rate_uses_default_estimate(rate):
    result = calculate_total_cost(
        "synthetic-embedding-example",
        {"prompt_tokens": 1000, "num_images": 2},
        fallback_pricing={"input_per_image": rate},
    )
    assert result["pricing_source"] == "default"
    assert result["total_cost"] == 0.00015


@pytest.mark.parametrize("image_key", ["input_per_image", "image_input_per_image"])
def test_image_only_pricing_without_token_rate(image_key):
    result = calculate_total_cost(
        "synthetic-embedding-example",
        {"num_images": 2},
        fallback_pricing={image_key: 0.002},
    )
    assert result["prompt_cost"] == 0.004
    assert result["total_cost"] == 0.004
