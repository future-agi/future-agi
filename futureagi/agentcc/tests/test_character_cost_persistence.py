"""Character-count availability must survive gateway cost persistence."""

from decimal import Decimal

import pytest

from agentcc.models import AgentccRequestLog
from agentcc.services.log_ingestion import ingest_request_logs
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing


def persist_cost(organization, metadata, **usage):
    entry = {
        "request_id": "character-count-policy",
        "model": "gemini-1.5-pro",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "status_code": 200,
        **usage,
        "metadata": {"org_id": str(organization.id), **metadata},
    }
    assert ingest_request_logs([entry]) == 1
    return AgentccRequestLog.no_workspace_objects.get(
        request_id=entry["request_id"]
    ).cost


@pytest.mark.django_db
@pytest.mark.parametrize("side", ["input", "output"])
@pytest.mark.parametrize("invalid", [None, "invalid", False, True, -2, "Infinity"])
def test_invalid_character_count_remains_unavailable(organization, side, invalid):
    metadata = {"input_characters": 50000, "output_characters": 10000}
    metadata[f"{side}_characters"] = invalid
    # An unavailable input uses the existing token estimate, not a fabricated
    # zero-character input. Valid input alone retains the existing TTS estimate.
    expected = Decimal("0.000450") if side == "input" else Decimal("0.750000")
    assert persist_cost(organization, metadata) == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"output_characters": 10000}, "0.000450"),
        ({"input_characters": 50000}, "0.750000"),
        ({}, "0.000450"),
        ({"input_characters": 0, "output_characters": 0}, "0.000000"),
    ],
)
def test_absent_and_explicit_zero_character_controls(organization, metadata, expected):
    assert persist_cost(organization, metadata) == Decimal(expected)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("input_count", "output_count", "expected"),
    [("50000", "10000", "0.420000"), (0, 10000, "0.070000")],
)
def test_character_amount_matches_independent_catalog_arithmetic(
    organization, input_count, output_count, expected
):
    pricing = get_model_pricing("gemini-1.5-pro")
    amount = sum(
        Decimal(str(pricing[f"{side}_per_1k_characters"])) * Decimal(count) / 1000
        for side, count in [("input", input_count), ("output", output_count)]
    )
    assert amount == Decimal(expected)
    assert (
        persist_cost(
            organization,
            {"input_characters": input_count, "output_characters": output_count},
            prompt_tokens=0,
            completion_tokens=0,
        )
        == amount
    )


@pytest.mark.django_db
def test_explicit_cache_discounted_gateway_cost_is_preserved(organization):
    # The gateway already supplies the cache-aware amount. Ingestion must not
    # replace it with an undiscounted fallback; this is a compatibility control.
    usage = {
        "prompt_tokens": 1000,
        "cached_input_tokens": 800,
        "completion_tokens": 100,
    }
    cost = calculate_total_cost("gemini-3.7-flash", usage)
    assert cost["total_cost"] == 0.000585
    assert persist_cost(
        organization, {}, model="gemini-3.7-flash", cost=cost["total_cost"], **usage
    ) == Decimal("0.000585")
