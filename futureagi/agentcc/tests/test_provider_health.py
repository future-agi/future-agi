"""An org provider's health is the org's own.

Each org provider's status comes from the org's own request logs of the last 24 h:
unhealthy at 50% errors or more, degraded at 10% or more, both only with 5
requests or more; fewer is healthy, with that reason. healthy = status !=
unhealthy, and circuit_state is "n/a" (the operator's breaker is never shown).
"""

import datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.request_log import AgentccRequestLog
from agentcc.tests.console_support import GATEWAY, FakeGateway, owner_of_new_org
from integrations.services.credentials import CredentialManager


@pytest.fixture
def gateway():
    fake = FakeGateway()
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


def _credential(organization, name, **fields):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=name,
        display_name=name.title(),
        encrypted_credentials=CredentialManager.encrypt({"api_key": f"sk-{name}-test"}),
        api_format="openai",
        models_list=[f"{name}-model"],
        **fields,
    )


@pytest.fixture
def openai(organization):
    return _credential(organization, "openai")


def _logs(organization, provider, total, errors, *, age_hours=1, **fields):
    started = timezone.now() - datetime.timedelta(hours=age_hours)
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=organization,
                provider=provider,
                model=f"{provider}-model",
                started_at=started,
                latency_ms=100,
                status_code=502 if i < errors else 200,
                is_error=i < errors,
                **fields,
            )
            for i in range(total)
        ]
    )


def _providers(auth_client):
    response = auth_client.get(f"{GATEWAY}/providers/")
    assert response.status_code == 200, response.content
    return {p["id"]: p for p in response.json()["result"]["providers"]}


def _assert_health_invariants(row):
    assert row["healthy"] is (row["status"] != "unhealthy")
    assert row["circuit_state"] == "n/a"
    assert row["status_reason"]


@pytest.mark.integration
def test_no_requests_is_healthy_with_the_reason(auth_client, openai, gateway):
    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert row["status_reason"] == "fewer than 5 requests in 24 h"
    _assert_health_invariants(row)


@pytest.mark.integration
def test_four_failed_requests_are_too_few_to_judge(
    organization, auth_client, openai, gateway
):
    _logs(organization, "openai", total=4, errors=4)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert row["status_reason"] == "fewer than 5 requests in 24 h"
    assert row["request_count"] == 4
    assert row["error_rate"] == 100.0
    _assert_health_invariants(row)


@pytest.mark.integration
def test_half_failed_is_unhealthy(organization, auth_client, openai, gateway):
    _logs(organization, "openai", total=10, errors=5)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "unhealthy"
    assert row["healthy"] is False
    assert row["error_rate"] == 50.0
    assert "50.0% of 10 requests failed" in row["status_reason"]
    _assert_health_invariants(row)


@pytest.mark.integration
def test_five_requests_all_failed_is_unhealthy(
    organization, auth_client, openai, gateway
):
    _logs(organization, "openai", total=5, errors=5)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "unhealthy"
    _assert_health_invariants(row)


@pytest.mark.integration
def test_ten_percent_failed_is_degraded(organization, auth_client, openai, gateway):
    _logs(organization, "openai", total=10, errors=1)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "degraded"
    assert row["healthy"] is True
    assert "10.0% of 10 requests failed" in row["status_reason"]
    _assert_health_invariants(row)


@pytest.mark.integration
def test_under_ten_percent_failed_is_healthy(
    organization, auth_client, openai, gateway
):
    _logs(organization, "openai", total=11, errors=1)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert "9.1% of 11 requests failed" in row["status_reason"]
    _assert_health_invariants(row)


@pytest.mark.integration
def test_the_operator_breaker_is_never_shown(
    organization, auth_client, openai, gateway
):
    # The gateway's global monitor says the operator's "openai" breaker is
    # open and its "anthropic" half-open: neither is the org's provider.
    _credential(organization, "anthropic")
    _logs(organization, "openai", total=20, errors=0)

    rows = _providers(auth_client)

    assert rows["openai"]["status"] == "healthy"
    assert rows["anthropic"]["status"] == "healthy"
    for row in rows.values():
        _assert_health_invariants(row)
    assert "provider_health" not in gateway.calls


@pytest.mark.integration
def test_only_the_last_24_hours_count(organization, auth_client, openai, gateway):
    _logs(organization, "openai", total=10, errors=10, age_hours=25)
    _logs(organization, "openai", total=10, errors=0)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert row["request_count"] == 10


@pytest.mark.integration
def test_another_orgs_failures_do_not_count(organization, auth_client, openai, gateway):
    other_org, _, _ = owner_of_new_org("Health Other Org")
    _logs(other_org, "openai", total=10, errors=10)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert row["request_count"] == 0


@pytest.mark.integration
def test_managed_traffic_under_the_same_name_does_not_count(
    organization, auth_client, openai, gateway
):
    # Requests the operator's managed "openai" served are not the org's
    # provider; a log row with no credential type is.
    _logs(organization, "openai", total=10, errors=10, credential_type="managed")
    _logs(organization, "openai", total=10, errors=0, credential_type="byok")
    _logs(organization, "openai", total=10, errors=0)

    row = _providers(auth_client)["openai"]

    assert row["status"] == "healthy"
    assert row["request_count"] == 20


@pytest.mark.integration
def test_guardrail_blocks_are_not_counted(organization, auth_client, openai, gateway):
    # The org's own guardrails refusing requests says nothing of the provider:
    # a block counts neither as a request, nor as a failure, nor in latency.
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=organization,
                provider="openai",
                started_at=timezone.now(),
                status_code=403,
                is_error=True,
                guardrail_triggered=True,
                latency_ms=1,
            )
            for _ in range(10)
        ]
    )
    _logs(organization, "openai", total=6, errors=3)

    row = _providers(auth_client)["openai"]

    assert row["request_count"] == 6
    assert row["error_rate"] == 50.0
    assert row["avg_latency"] == 100.0
    assert row["status"] == "unhealthy"
    assert "50.0% of 6 requests failed" in row["status_reason"]


@pytest.mark.integration
def test_a_403_that_is_not_a_guardrail_block_counts(
    organization, auth_client, openai, gateway
):
    # Only a block with a triggered check is left out; any other 403 failed.
    _logs(organization, "openai", total=10, errors=0)
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=organization,
                provider="openai",
                started_at=timezone.now(),
                status_code=403,
                is_error=True,
            )
            for _ in range(2)
        ]
    )

    row = _providers(auth_client)["openai"]

    assert row["request_count"] == 12
    assert row["status"] == "degraded"


@pytest.mark.integration
def test_the_action_needs_no_gateway_answer(organization, auth_client, openai):
    # Health comes from the request logs; a gateway that cannot be reached
    # changes nothing here.
    from agentcc.services.gateway_client import GatewayClientError

    with patch(
        "agentcc.views.gateway.get_gateway_client",
        side_effect=GatewayClientError("Connection refused"),
    ):
        _logs(organization, "openai", total=10, errors=6)
        row = _providers(auth_client)["openai"]

    assert row["status"] == "unhealthy"
    _assert_health_invariants(row)
