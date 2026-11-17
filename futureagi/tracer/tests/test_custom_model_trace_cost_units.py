"""Custom trace prices use the same per-million units as model entry."""

import pytest
from django.core.cache import cache

from model_hub.models.custom_models import CustomAIModel
from tracer.utils.otel import calculate_cost_from_tokens, model_cost


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "expected_cost"),
    [
        pytest.param(1_000_000, 0, 2.0, id="prompt"),
        pytest.param(0, 1_000_000, 3.0, id="completion"),
        pytest.param(1_000_000, 1_000_000, 5.0, id="combined"),
    ],
)
def test_custom_model_trace_cost_uses_per_million_prices(
    organization, prompt_tokens, completion_tokens, expected_cost
):
    model = "trace-custom-per-million-regression"
    assert model not in model_cost
    CustomAIModel.objects.create(
        user_model_id=model,
        provider="openai",
        input_token_cost=2.0,
        output_token_cost=3.0,
        organization=organization,
    )
    organization_id = str(organization.id)
    cache_key = f"custom_model_pricing:{organization_id}:{model}"
    cache.delete(cache_key)
    try:
        # Exercise both the database lookup and the cached pricing path.
        for _ in range(2):
            assert calculate_cost_from_tokens(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                organization_id=organization_id,
            ) == pytest.approx(expected_cost)
    finally:
        cache.delete(cache_key)
