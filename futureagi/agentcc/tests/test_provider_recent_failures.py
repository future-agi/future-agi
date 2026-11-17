"""The providers read's recent failures: per org provider, the org's own
calls of the last 5 minutes (recent_calls) and how many of them failed
(recent_failures), counted as the 24 h status counts them: a managed call
and a guardrail block are not the provider's, and another org's calls never
count."""

import datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.request_log import AgentccRequestLog
from agentcc.tests.r7_console_a_support import GATEWAY, FakeGateway, owner_of_new_org
from integrations.services.credentials import CredentialManager


@pytest.fixture
def gateway():
    fake = FakeGateway()
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


@pytest.fixture
def openai(organization):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name="openai",
        display_name="Openai",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-openai-test"}),
        api_format="openai",
        models_list=["openai-model"],
    )


def _logs(organization, total, errors, *, age_minutes=1, **fields):
    started = timezone.now() - datetime.timedelta(minutes=age_minutes)
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=organization,
                provider="openai",
                model="openai-model",
                started_at=started,
                latency_ms=100,
                status_code=502 if i < errors else 200,
                is_error=i < errors,
                **fields,
            )
            for i in range(total)
        ]
    )


def _openai(auth_client):
    response = auth_client.get(f"{GATEWAY}/providers/")
    assert response.status_code == 200, response.content
    rows = {p["id"]: p for p in response.json()["result"]["providers"]}
    return rows["openai"]


@pytest.mark.integration
def test_counts_the_calls_and_failures_of_the_last_five_minutes(
    organization, auth_client, openai, gateway
):
    _logs(organization, total=8, errors=3, age_minutes=1)
    _logs(organization, total=10, errors=10, age_minutes=30)

    row = _openai(auth_client)

    assert row["recent_calls"] == 8
    assert row["recent_failures"] == 3
    # The 24 h figures still count both.
    assert row["request_count"] == 18


@pytest.mark.integration
def test_no_recent_call_is_zero_of_zero(organization, auth_client, openai, gateway):
    _logs(organization, total=10, errors=10, age_minutes=30)

    row = _openai(auth_client)

    assert row["recent_calls"] == 0
    assert row["recent_failures"] == 0


@pytest.mark.integration
def test_another_orgs_calls_never_count(organization, auth_client, openai, gateway):
    other_org, _, _ = owner_of_new_org("Recent Failures Other Org")
    _logs(other_org, total=10, errors=10)
    _logs(organization, total=4, errors=0)

    row = _openai(auth_client)

    assert row["recent_calls"] == 4
    assert row["recent_failures"] == 0


@pytest.mark.integration
def test_managed_calls_and_guardrail_blocks_are_not_the_providers(
    organization, auth_client, openai, gateway
):
    _logs(organization, total=5, errors=5, credential_type="managed")
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
            for _ in range(5)
        ]
    )
    _logs(organization, total=6, errors=2, credential_type="byok")

    row = _openai(auth_client)

    assert row["recent_calls"] == 6
    assert row["recent_failures"] == 2
