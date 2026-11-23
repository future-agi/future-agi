"""TH-10432: Provider Health status derived from the gateway circuit state.

The Go gateway reports a recovering provider as ``circuit_state="half-open"``
with ``healthy=true``. The providers endpoint must surface that as
``status="degraded"`` (not ``"healthy"``), while still returning the
gateway's own circuit_state value.
"""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.views.gateway import _normalize_circuit_state
from integrations.services.credentials import CredentialManager

PROVIDER = "openai"

# (gateway health entry or None, expected status, expected circuit_state)
CASES = {
    "half_open_hyphen_healthy": (
        {"id": PROVIDER, "healthy": True, "circuit_state": "half-open"},
        "degraded",
        "half-open",
    ),
    "half_open_underscore": (
        {"id": PROVIDER, "healthy": True, "circuit_state": "half_open"},
        "degraded",
        "half_open",
    ),
    "half_open_upper": (
        {"id": PROVIDER, "healthy": True, "circuit_state": "HALF-OPEN"},
        "degraded",
        "half-open",
    ),
    "open": (
        {"id": PROVIDER, "healthy": False, "circuit_state": "open"},
        "unhealthy",
        "open",
    ),
    "open_but_healthy_flag": (
        {"id": PROVIDER, "healthy": True, "circuit_state": "open"},
        "unhealthy",
        "open",
    ),
    "unhealthy_flag": (
        {"id": PROVIDER, "healthy": False, "circuit_state": "closed"},
        "unhealthy",
        "closed",
    ),
    "unhealthy_flag_half_open": (
        {"id": PROVIDER, "healthy": False, "circuit_state": "half-open"},
        "unhealthy",
        "half-open",
    ),
    "closed": (
        {"id": PROVIDER, "healthy": True, "circuit_state": "closed"},
        "healthy",
        "closed",
    ),
    "missing_circuit_state": (
        {"id": PROVIDER, "healthy": True},
        "healthy",
        "n/a",
    ),
    "missing_provider_entry": (None, "healthy", "n/a"),
}


@pytest.fixture
def provider_credential(user):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=user.organization,
        provider_name=PROVIDER,
        display_name="OpenAI",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-test"}),
        api_format="openai",
        models_list=["gpt-4o"],
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
@patch("agentcc.views.gateway.get_gateway_client")
def test_provider_status_follows_circuit_state(
    mock_get_client, case, auth_client, provider_credential
):
    entry, expected_status, expected_circuit = CASES[case]
    mock_client = MagicMock()
    mock_client.provider_health.return_value = {
        "providers": [entry] if entry is not None else []
    }
    mock_get_client.return_value = mock_client

    response = auth_client.get("/agentcc/gateways/default/providers/")

    assert response.status_code == 200, response.json()
    providers = response.json()["result"]["providers"]
    assert len(providers) == 1
    provider = providers[0]
    assert provider["id"] == PROVIDER
    assert provider["status"] == expected_status
    assert provider["circuit_state"] == expected_circuit


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("half-open", "half_open"),
        ("half_open", "half_open"),
        ("Half-Open", "half_open"),
        (" HALF_OPEN ", "half_open"),
        ("half open", "half_open"),
        ("open", "open"),
        ("OPEN", "open"),
        ("closed", "closed"),
        ("n/a", "n/a"),
        ("", ""),
        (None, ""),
        (3, ""),
    ],
)
def test_normalize_circuit_state(raw, expected):
    assert _normalize_circuit_state(raw) == expected
