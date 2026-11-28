"""Direct provider writes: reject negative limits without changing zero or defaults."""

from unittest.mock import patch

import pytest

from agentcc.models import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = [pytest.mark.integration, pytest.mark.api]

URL = "/agentcc/provider-credentials/"
DEFAULTS = {"default_timeout_seconds": 60, "max_concurrent": 100, "conn_pool_size": 100}


@pytest.fixture
def push():
    with patch(
        "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
        return_value=True,
    ) as mocked:
        yield mocked


@pytest.fixture
def credential(organization):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name="limit-check",
        display_name="Before",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "fixture"}),
    )


@pytest.mark.parametrize("field", DEFAULTS)
def test_create_rejects_negative_limit(auth_client, organization, push, field):
    rows = AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    )
    before = rows.count()
    response = auth_client.post(
        URL,
        {
            "provider_name": "limit-check",
            "credentials": {"api_key": "fixture"},
            field: -1,
        },
        format="json",
    )
    assert response.status_code == 400, response.data
    assert rows.count() == before
    push.assert_not_called()


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("field", DEFAULTS)
def test_update_rejects_negative_limit(auth_client, credential, push, method, field):
    rows = AgentccProviderCredential.no_workspace_objects.filter(pk=credential.pk)
    before = rows.values().get()
    response = getattr(auth_client, method)(
        f"{URL}{credential.id}/", {"display_name": "After", field: -1}, format="json"
    )
    assert response.status_code == 400, response.data
    assert rows.values().get() == before
    push.assert_not_called()


@pytest.mark.parametrize("value", [None, 0, 7])
def test_create_preserves_supported_limits(auth_client, organization, push, value):
    limits = {} if value is None else dict.fromkeys(DEFAULTS, value)
    response = auth_client.post(
        URL,
        {
            "provider_name": "limit-check",
            "credentials": {"api_key": "fixture"},
            **limits,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    saved = AgentccProviderCredential.no_workspace_objects.values(*DEFAULTS).get(
        organization=organization, provider_name="limit-check"
    )
    assert saved == (limits or DEFAULTS)
    push.assert_called_once()


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("value", [None, 0, 7])
def test_update_validates_only_submitted_limits(
    auth_client, credential, push, method, value
):
    # Existing out-of-range values do not block editing unrelated metadata.
    rows = AgentccProviderCredential.no_workspace_objects.filter(pk=credential.pk)
    legacy = dict.fromkeys(DEFAULTS, -2)
    rows.update(**legacy)
    limits = {} if value is None else dict.fromkeys(DEFAULTS, value)
    response = getattr(auth_client, method)(
        f"{URL}{credential.id}/", {"display_name": "After", **limits}, format="json"
    )
    assert response.status_code == 200, response.data
    assert rows.values(*DEFAULTS).get() == (limits or legacy)
    push.assert_called_once()
