from unittest.mock import patch

import pytest

from agentcc.models import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = [pytest.mark.integration, pytest.mark.api]

URL = "/agentcc/gateways/default/update-provider/"
NAME = "provider-limit-validation"
LIMITS = ("default_timeout_seconds", "max_concurrent", "conn_pool_size")


@pytest.fixture
def push():
    with patch(
        "agentcc.views.gateway.AgentccGatewayViewSet._push_current_config",
        return_value=True,
    ) as mocked:
        yield mocked


@pytest.fixture
def rows(organization):
    return AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization, provider_name=NAME
    )


@pytest.fixture
def credential(organization):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=NAME,
        display_name="Before",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "fixture"}),
        default_timeout_seconds=25,
        max_concurrent=30,
        conn_pool_size=45,
    )


@pytest.mark.parametrize("field", ["default_timeout", *LIMITS])
def test_negative_limit_does_not_create(auth_client, rows, push, field):
    response = auth_client.post(
        URL, {"name": NAME, "config": {"api_key": "fixture", field: -1}}, format="json"
    )
    assert response.status_code == 400, response.data
    assert not rows.exists()
    push.assert_not_called()


@pytest.mark.parametrize(
    "limits",
    [
        {"default_timeout": -1},
        {"default_timeout_seconds": -1},
        {"max_concurrent": "-1"},
        {"conn_pool_size": -1},
        {"default_timeout": -1, "default_timeout_seconds": 30},
        {"default_timeout": 30, "default_timeout_seconds": -1},
    ],
)
def test_negative_limit_does_not_update_or_push(
    auth_client, credential, rows, push, limits
):
    before = rows.values().get()
    response = auth_client.post(
        URL,
        {
            "name": NAME,
            "config": {"display_name": "After", "api_key": "replacement", **limits},
        },
        format="json",
    )
    assert response.status_code == 400, response.data
    assert rows.values().get() == before
    push.assert_not_called()


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({}, (60, 100, 100)),
        ({"default_timeout": 0, "max_concurrent": 0, "conn_pool_size": 0}, (0, 0, 0)),
        ({"default_timeout": 9, "default_timeout_seconds": 0}, (0, 100, 100)),
        ({"default_timeout": 7, "max_concurrent": 8, "conn_pool_size": 9}, (7, 8, 9)),
        ({"default_timeout": 6, "default_timeout_seconds": 7}, (7, 100, 100)),
    ],
)
def test_supported_limits_keep_dashboard_defaults_and_precedence(
    auth_client, credential, rows, push, limits, expected
):
    response = auth_client.post(
        URL,
        {"name": NAME, "config": {"display_name": "After", **limits}},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert rows.values_list(*LIMITS).get() == expected
    push.assert_called_once()
