import pytest

from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.models.webhook import AgentccWebhook
from agentcc.serializers.api_key import (
    AgentccAPIKeyCreateSerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.serializers.custom_property import AgentccCustomPropertySchemaSerializer
from agentcc.serializers.webhook import AgentccWebhookSerializer

INVALID_NAME = '<script>alert("xss")</script>'
VALID_NAME = "Safe_Name-123"
NAME_ERROR = "Name can only contain letters, numbers, hyphens, and underscores"


@pytest.mark.integration
class TestAgentccSafeNameValidationSerializers:
    def test_api_key_create_serializer_rejects_xss_name(self):
        serializer = AgentccAPIKeyCreateSerializer(data={"name": INVALID_NAME})

        assert not serializer.is_valid()
        assert serializer.errors["name"][0] == NAME_ERROR

    def test_api_key_update_serializer_rejects_xss_name(self):
        serializer = AgentccAPIKeyUpdateSerializer(data={"name": INVALID_NAME})

        assert not serializer.is_valid()
        assert serializer.errors["name"][0] == NAME_ERROR

    def test_webhook_serializer_rejects_xss_name(self, user):
        serializer = AgentccWebhookSerializer(
            data={
                "name": INVALID_NAME,
                "url": "https://example.com/webhook",
                "events": ["request.completed"],
            }
        )

        assert not serializer.is_valid()
        assert serializer.errors["name"][0] == NAME_ERROR

    def test_webhook_serializer_allows_safe_name(self):
        serializer = AgentccWebhookSerializer(
            data={
                "name": VALID_NAME,
                "url": "https://example.com/webhook",
                "events": ["request.completed"],
            }
        )

        assert serializer.is_valid(), serializer.errors

    def test_custom_property_serializer_rejects_xss_name(self):
        serializer = AgentccCustomPropertySchemaSerializer(
            data={"name": INVALID_NAME, "property_type": "string"}
        )

        assert not serializer.is_valid()
        assert serializer.errors["name"][0] == NAME_ERROR

    def test_custom_property_serializer_allows_safe_name(self):
        serializer = AgentccCustomPropertySchemaSerializer(
            data={"name": VALID_NAME, "property_type": "string"}
        )

        assert serializer.is_valid(), serializer.errors

    def test_custom_property_serializer_rejects_enum_without_values(self):
        serializer = AgentccCustomPropertySchemaSerializer(
            data={"name": VALID_NAME, "property_type": "enum", "allowed_values": []}
        )

        assert not serializer.is_valid()
        assert "allowed_values" in serializer.errors

    def test_custom_property_serializer_rejects_enum_default_outside_values(self):
        serializer = AgentccCustomPropertySchemaSerializer(
            data={
                "name": VALID_NAME,
                "property_type": "enum",
                "allowed_values": ["alpha", "beta"],
                "default_value": "gamma",
            }
        )

        assert not serializer.is_valid()
        assert "default_value" in serializer.errors

    def test_custom_property_serializer_rejects_boolean_as_number_default(self):
        serializer = AgentccCustomPropertySchemaSerializer(
            data={
                "name": VALID_NAME,
                "property_type": "number",
                "default_value": True,
            }
        )

        assert not serializer.is_valid()
        assert "default_value" in serializer.errors
