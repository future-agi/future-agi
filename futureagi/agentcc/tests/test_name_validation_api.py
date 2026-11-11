from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models import AgentccAPIKey
from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.models.webhook import AgentccWebhook

INVALID_NAME = '<script>alert("xss")</script>'
NAME_ERROR = "Name can only contain letters, numbers, hyphens, and underscores"


@pytest.fixture
def gateway_id():
    return "default"


@pytest.mark.integration
@pytest.mark.api
class TestAgentccSafeNameValidationAPI:
    @patch("agentcc.views.api_key.auth_bridge")
    def test_create_api_key_rejects_xss_name(
        self, mock_bridge, auth_client, gateway_id
    ):
        response = auth_client.post(
            "/agentcc/api-keys/",
            {"gateway_id": gateway_id, "name": INVALID_NAME},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert NAME_ERROR in response.json()["result"]
        mock_bridge.provision_key.assert_not_called()

    @patch("agentcc.views.api_key.auth_bridge")
    def test_update_api_key_rejects_xss_name(self, mock_bridge, auth_client, user):
        api_key = AgentccAPIKey.objects.create(
            organization=user.organization,
            workspace=None,
            gateway_key_id="gw-key-1",
            key_prefix="pk-test",
            name="safe_name",
        )

        response = auth_client.patch(
            f"/agentcc/api-keys/{api_key.id}/",
            {"name": INVALID_NAME},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert NAME_ERROR in response.json()["result"]
        mock_bridge.update_key.assert_not_called()

    @pytest.mark.requires_ee
    def test_create_webhook_rejects_xss_name(self, auth_client):
        response = auth_client.post(
            "/agentcc/webhooks/",
            {
                "name": INVALID_NAME,
                "url": "https://example.com/webhook",
                "events": ["request.completed"],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["name"][0] == NAME_ERROR

    def test_update_webhook_rejects_xss_name(self, auth_client, user):
        webhook = AgentccWebhook.objects.create(
            organization=user.organization,
            name="safe_name",
            url="https://example.com/webhook",
            events=["request.completed"],
        )

        response = auth_client.patch(
            f"/agentcc/webhooks/{webhook.id}/",
            {"name": INVALID_NAME},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["name"][0] == NAME_ERROR

    def test_create_custom_property_rejects_xss_name(self, auth_client):
        response = auth_client.post(
            "/agentcc/custom-properties/",
            {
                "name": INVALID_NAME,
                "property_type": "string",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["name"][0] == NAME_ERROR

    def test_update_custom_property_rejects_xss_name(self, auth_client, user):
        schema = AgentccCustomPropertySchema.objects.create(
            organization=user.organization,
            name="safe_name",
            property_type="string",
        )

        response = auth_client.patch(
            f"/agentcc/custom-properties/{schema.id}/",
            {"name": INVALID_NAME},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["name"][0] == NAME_ERROR
