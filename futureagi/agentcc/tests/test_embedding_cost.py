"""Embedding cost fallback must reach stored gateway request logs."""

from decimal import Decimal

import pytest

from agentcc.models import AgentccRequestLog
from agentcc.services.log_ingestion import _compute_cost, ingest_request_logs


@pytest.mark.parametrize(
    ("model", "tokens", "metadata", "expected"),
    [
        ("text-embedding-3-small", 1000, {}, "0.000020"),
        ("text-embedding-3-large", 1000, {}, "0.000400"),
        ("text-embedding-ada-002", 1000, {}, "0.000100"),
        ("azure_ai/mistral-large", 1000, {}, "0.000150"),
        ("amazon.titan-embed-image-v1", 0, {"num_images": "5"}, "0.000300"),
        ("amazon.titan-embed-image-v1", 1000, {"num_images": 5}, "0.001100"),
    ],
)
@pytest.mark.django_db
def test_embedding_cost_is_persisted(organization, model, tokens, metadata, expected):
    metadata = {**metadata, "org_id": str(organization.id)}
    entry = {
        "request_id": "embedding-cost-example",
        "model": model,
        "prompt_tokens": tokens,
        "metadata": metadata,
        "status_code": 200,
    }

    assert ingest_request_logs([entry]) == 1
    stored = AgentccRequestLog.no_workspace_objects.get(request_id=entry["request_id"])
    assert stored.cost == Decimal(expected)
    source = "default" if model == "azure_ai/mistral-large" else "available_models"
    assert stored.metadata == {**metadata, "pricing_source": source}
    assert "pricing_source" not in metadata  # The caller's payload stays unchanged.


@pytest.mark.parametrize(
    ("input_characters", "output_characters", "expected"),
    [(50000, "10000", "0.420000"), (50000, 0, "0.350000"), (0, 10000, "0.070000")],
)
@pytest.mark.django_db
def test_both_character_counts_reach_persisted_cost(
    organization, input_characters, output_characters, expected
):
    entry = {
        "request_id": "character-cost-example",
        "model": "gemini-1.5-pro",
        "metadata": {
            "org_id": str(organization.id),
            "input_characters": input_characters,
            "output_characters": output_characters,
        },
        "status_code": 200,
    }

    assert ingest_request_logs([entry]) == 1
    stored = AgentccRequestLog.no_workspace_objects.get(request_id=entry["request_id"])
    assert stored.cost == Decimal(expected)


def test_input_image_count_does_not_replace_explicit_gateway_cost():
    entry = {
        "model": "amazon.titan-embed-image-v1",
        "cost": 0.25,
        "metadata": {"num_images": 5},
    }
    assert _compute_cost(entry) == 0.25


@pytest.mark.parametrize("count", [None, "invalid", -2, float("inf")])
def test_invalid_input_image_count_keeps_embedding_token_cost(count):
    entry = {
        "model": "amazon.titan-embed-image-v1",
        "prompt_tokens": 1000,
        "metadata": {"num_images": count},
    }
    assert _compute_cost(entry) == 0.0008
