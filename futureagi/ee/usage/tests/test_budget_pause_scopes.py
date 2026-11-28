"""Budget pauses apply to spend, not to trace ingestion or storage."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ee.usage.services import metering


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event", "dimension", "pausable"),
    [
        ("tracing_event", "tracing_events", False),
        ("observe_add", "storage", False),
        ("turing_large_evaluator", "ai_credits", True),
        ("text_call", "text_sim_tokens", True),
        ("voice_call", "voice_sim_minutes", True),
        ("gateway_request", "gateway_requests", True),
    ],
)
def test_budget_pause_scopes(monkeypatch, event, dimension, pausable):
    # Supply the resolved dimension without depending on the cloud billing.yaml.
    config = Mock()
    config.get_call_type.return_value = SimpleNamespace(dimension=dimension, per_call=1)
    config.get_plan.return_value = SimpleNamespace(usage_caps="soft")
    monkeypatch.setattr(metering.BillingConfig, "get", lambda: config)

    for scopes in [("total_spend",), (dimension,), (dimension, "total_spend")]:
        keys = {"plan:org": "payg", "billing_status:org": "active"}
        keys.update({f"pause:org:{scope}": "1" for scope in scopes})
        monkeypatch.setattr(metering, "get_redis", Mock(return_value=keys))
        result = metering.check_usage("org", event)
        assert result.dimension == dimension
        assert result.allowed is (not pausable), scopes
        if pausable:
            assert result.error_code == "BUDGET_PAUSED", scopes
