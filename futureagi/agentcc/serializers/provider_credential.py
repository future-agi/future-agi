from rest_framework import serializers

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.credential_manager import mask_key
from integrations.services.credentials import CredentialManager


class AgentccProviderCredentialSerializer(serializers.ModelSerializer):
    """Read serializer — decrypts credentials and masks sensitive values."""

    credentials = serializers.SerializerMethodField()

    class Meta:
        model = AgentccProviderCredential
        fields = [
            "id",
            "organization",
            "workspace",
            "provider_name",
            "display_name",
            "credentials",
            "base_url",
            "api_format",
            "models_list",
            "default_timeout_seconds",
            "max_concurrent",
            "conn_pool_size",
            "extra_config",
            "is_active",
            "last_rotated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]

    def get_credentials(self, obj):
        """Decrypt and mask credentials for display."""
        try:
            data = CredentialManager.decrypt(bytes(obj.encrypted_credentials))
        except Exception:
            return {}
        masked = {}
        for key, value in data.items():
            if isinstance(value, str) and len(value) > 4:
                masked[key] = mask_key(value)
            else:
                masked[key] = value
        return masked


class AgentccProviderCredentialCreateSerializer(serializers.Serializer):
    provider_name = serializers.CharField(max_length=100)
    display_name = serializers.CharField(max_length=255, required=False, default="")
    credentials = serializers.DictField(child=serializers.CharField())
    base_url = serializers.URLField(max_length=500, required=False, default="")
    api_format = serializers.CharField(max_length=50, required=False, default="openai")
    models_list = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    default_timeout_seconds = serializers.IntegerField(
        required=False, default=60, min_value=1
    )
    max_concurrent = serializers.IntegerField(required=False, default=100, min_value=1)
    conn_pool_size = serializers.IntegerField(required=False, default=100, min_value=1)
    extra_config = serializers.DictField(required=False, default=dict)

    def validate_credentials(self, value):
        if not value:
            raise serializers.ValidationError("credentials must not be empty")
        if "api_key" not in value:
            raise serializers.ValidationError(
                "credentials must contain at least an 'api_key' field"
            )
        return value


class AgentccProviderCredentialUpdateSerializer(serializers.Serializer):
    """Partial update serializer — credentials can be rotated via the rotate action."""

    display_name = serializers.CharField(max_length=255, required=False)
    base_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    api_format = serializers.CharField(max_length=50, required=False)
    models_list = serializers.ListField(child=serializers.CharField(), required=False)
    default_timeout_seconds = serializers.IntegerField(required=False, min_value=1)
    max_concurrent = serializers.IntegerField(required=False, min_value=1)
    conn_pool_size = serializers.IntegerField(required=False, min_value=1)
    extra_config = serializers.DictField(required=False)
    is_active = serializers.BooleanField(required=False)
