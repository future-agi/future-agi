"""Gateway log estimates retain their source alongside stored amounts."""

import pytest

from agentcc.models import AgentccRequestLog
from agentcc.services.log_ingestion import _compute_cost, ingest_request_logs


def test_known_model_price_wins_over_unknown_resolved_alias():
    assert (
        _compute_cost(
            {
                "resolved_model": "unknown-deployment-alias",
                "model": "text-embedding-3-small",
                "prompt_tokens": 1000,
            }
        )
        == 0.00002
    )


def test_known_zero_resolved_price_is_not_replaced(monkeypatch):
    monkeypatch.setattr(
        "agentic_eval.core_evals.run_prompt.model_pricing.get_model_pricing",
        lambda model: {
            "input_per_1M_tokens": 0 if model == "free" else 1,
            "output_per_1M_tokens": 0,
        },
    )
    assert (
        _compute_cost(
            {
                "resolved_model": "free",
                "model": "priced",
                "prompt_tokens": 1000,
            }
        )
        == 0
    )


@pytest.mark.django_db
def test_reported_gateway_cost_source_is_persisted(organization):
    entry = {
        "request_id": "reported-cost-source-example",
        "model": "text-embedding-3-small",
        "prompt_tokens": 1000,
        "cost": 0.25,
        "metadata": {"org_id": str(organization.id)},
        "status_code": 200,
    }
    assert ingest_request_logs([entry]) == 1
    stored = AgentccRequestLog.no_workspace_objects.get(request_id=entry["request_id"])
    assert stored.metadata["pricing_source"] == "gateway"
    assert float(stored.cost) == 0.25
