from rest_framework import serializers

from agentcc.models import AgentccAPIKey
from agentcc.validators import validate_safe_agentcc_name
from tfc.utils.api_serializers import StrictInputSerializer


class AgentccAPIKeySerializer(serializers.ModelSerializer):
    """An AgentCC gateway API key — the credential callers use to route LLM
    requests through the gateway, optionally restricted to specific models or
    providers. Listed/read via list_agentcc_api_keys / get_agentcc_api_key; the
    raw secret is never returned (only key_prefix and gateway_key_id). status is
    active/revoked/expired."""

    class Meta:
        model = AgentccAPIKey
        fields = [
            "id",
            "project",
            "user",
            "gateway_key_id",
            "key_prefix",
            "name",
            "owner",
            "status",
            "allowed_models",
            "allowed_providers",
            "metadata",
            "last_used_at",
            "expires_at",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "gateway_key_id",
            "key_prefix",
            "status",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "project": {
                "help_text": "UUID of the AgentCC project this key belongs to (optional)."
            },
            "user": {"help_text": "UUID of the user who owns this key (optional)."},
            "gateway_key_id": {
                "help_text": "Gateway-side identifier for the key (read-only)."
            },
            "key_prefix": {
                "help_text": "Short non-secret prefix of the key for display (read-only)."
            },
            "name": {"help_text": "Human-readable name for this API key."},
            "owner": {"help_text": "Free-text owner/team label for the key."},
            "status": {
                "help_text": "Key state: active, revoked, or expired (read-only)."
            },
            "allowed_models": {
                "help_text": "JSON array of model names this key may call; empty means all allowed."
            },
            "allowed_providers": {
                "help_text": "JSON array of provider names this key may call; empty means all allowed."
            },
            "metadata": {"help_text": "Arbitrary JSON object of key-value metadata."},
            "last_used_at": {
                "help_text": "Timestamp the key was last used (read-only)."
            },
            "expires_at": {
                "help_text": "Optional expiry timestamp after which the key stops working."
            },
        }


class AgentccAPIKeyUpdateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=255, required=False)
    owner = serializers.CharField(max_length=255, required=False, allow_blank=True)
    allowed_models = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    allowed_providers = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    metadata = serializers.DictField(required=False)

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))


class AgentccAPIKeyCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    owner = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    allowed_models = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    allowed_providers = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    metadata = serializers.DictField(required=False, default=dict)

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
