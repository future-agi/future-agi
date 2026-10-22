from urllib.parse import urlsplit

from django.core.validators import URLValidator
from rest_framework import serializers

from integrations.services.credentials import CredentialManager
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.credential_manager import mask_key


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


class ProviderBaseURLField(serializers.URLField):
    """An http(s) URL with a host, which may be a single label.

    Django's URLValidator wants a dotted domain, so it refused a provider on
    the Docker network or in the cluster (http://ollama:11434, a Kubernetes
    Service short name) that the gateway UI saves. Whether the host may be
    called is ``ensure_provider_base_url_allowed``'s decision, in the view.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.validators = [
            v for v in self.validators if not isinstance(v, URLValidator)
        ]

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            parts = urlsplit(value)
            _ = parts.port  # raises unless the port is a number in 0-65535
        except ValueError:
            self.fail("invalid")
        if (
            parts.scheme.lower() not in ("http", "https")
            or not parts.hostname
            # The gateway's url.Parse refuses control characters.
            or any(ch.isspace() or not ch.isprintable() for ch in value)
        ):
            self.fail("invalid")
        return value


class AgentccProviderCredentialCreateSerializer(serializers.Serializer):
    provider_name = serializers.CharField(max_length=100)
    display_name = serializers.CharField(max_length=255, required=False, default="")
    credentials = serializers.DictField(child=serializers.CharField())
    base_url = ProviderBaseURLField(max_length=500, required=False, default="")
    api_format = serializers.CharField(max_length=50, required=False, default="openai")
    models_list = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    default_timeout_seconds = serializers.IntegerField(required=False, default=60)
    max_concurrent = serializers.IntegerField(required=False, default=100)
    conn_pool_size = serializers.IntegerField(required=False, default=100)
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
    base_url = ProviderBaseURLField(max_length=500, required=False, allow_blank=True)
    api_format = serializers.CharField(max_length=50, required=False)
    models_list = serializers.ListField(child=serializers.CharField(), required=False)
    default_timeout_seconds = serializers.IntegerField(required=False)
    max_concurrent = serializers.IntegerField(required=False)
    conn_pool_size = serializers.IntegerField(required=False)
    extra_config = serializers.DictField(required=False)
    is_active = serializers.BooleanField(required=False)
