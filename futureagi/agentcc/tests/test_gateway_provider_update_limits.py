from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models import AgentccProviderCredential
from integrations.services.credentials import CredentialManager


@pytest.mark.integration
@pytest.mark.api
class TestGatewayProviderUpdateLimits:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_rejects_non_positive_limits_before_write_or_push(
        self,
        mock_push_config,
        auth_client,
        organization,
    ):
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="provider-limit-validation",
            display_name="Provider Limit Validation",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "sk-provider-limit-validation"}
            ),
            api_format="openai",
            default_timeout_seconds=60,
            max_concurrent=100,
            conn_pool_size=100,
        )
        encrypted_before = bytes(credential.encrypted_credentials)

        for field_name, value in (
            ("default_timeout", 0),
            ("default_timeout_seconds", -1),
            ("max_concurrent", 0),
            ("conn_pool_size", -1),
        ):
            response = auth_client.post(
                "/agentcc/gateways/default/update-provider/",
                {
                    "name": credential.provider_name,
                    "config": {field_name: value},
                },
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST

        mock_push_config.assert_not_called()
        credential.refresh_from_db()
        assert credential.default_timeout_seconds == 60
        assert credential.max_concurrent == 100
        assert credential.conn_pool_size == 100
        assert bytes(credential.encrypted_credentials) == encrypted_before
