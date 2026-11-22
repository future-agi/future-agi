"""Independent field errors on real gateway provider updates."""

from unittest.mock import patch

import pytest

from agentcc.models import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.mark.parametrize(
    ("stored", "submitted", "typo", "expect_limit_error"),
    [
        pytest.param(None, -1, True, True, id="new-negative-with-typo"),
        pytest.param(-2, -1, True, True, id="changed-negative-with-typo"),
        pytest.param(None, -1, False, True, id="negative-without-typo"),
        pytest.param(-1, -1, True, False, id="echo-with-typo"),
        pytest.param(None, 100, True, False, id="valid-with-typo"),
    ],
)
def test_unknown_field_and_pool_limit(
    auth_client, organization, stored, submitted, typo, expect_limit_error
):
    name = "sample"
    if stored is not None:
        AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name=name,
            conn_pool_size=stored,
            encrypted_credentials=CredentialManager.encrypt({"api_key": "fixture"}),
        )
        readback = auth_client.get("/agentcc/gateways/default/config/")
        assert readback.status_code == 200
        current = readback.data["result"]["providers"][name]["conn_pool_size"]
        assert current == stored
        if current == submitted:
            submitted = current

    rows = AgentccProviderCredential._base_manager.filter(
        organization=organization
    ).order_by("pk")
    before = list(rows.values())
    payload = {"name": name, "config": {"conn_pool_size": submitted}}
    expected = {}
    if typo:
        payload["typo"] = True
        expected["typo"] = ["Unknown field."]
    if expect_limit_error:
        expected["config"] = ["conn_pool_size: Must be greater than or equal to zero."]
    with patch(
        "agentcc.views.gateway.AgentccGatewayViewSet._push_current_config"
    ) as push:
        response = auth_client.post(
            "/agentcc/gateways/default/update-provider/", payload, format="json"
        )
        assert response.status_code == 400
        assert list(rows.values()) == before
        push.assert_not_called()
        assert response.data["details"] == expected
