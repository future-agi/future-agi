from urllib.parse import urlsplit

from rest_framework import serializers

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.config_push import (
    UnsupportedProviderCredentialFields,
    _normalize_provider_credentials,
)
from agentcc.services.credential_manager import mask_key
from agentcc.services.provider_validation import (
    ALLOWED_CREDENTIAL_KEYS,
    PROVIDER_API_FORMATS,
    PROVIDER_FORMAT_ERROR,
    ensure_provider_configuration,
)
from integrations.services.credentials import CredentialManager
from tfc.utils.serializer_fields import JsonValueField


def unsupported_credential_keys(provider_name, credentials, *, for_save=True):
    """New credentials accept secrets and their aliases, not provider settings.

    Health reporting uses the push contract for legacy rows so tightening new
    saves does not misreport an existing provider as missing from the gateway.
    """
    if for_save:
        return tuple(sorted(set(credentials) - set(ALLOWED_CREDENTIAL_KEYS)))
    try:
        _normalize_provider_credentials(provider_name, credentials)
    except UnsupportedProviderCredentialFields as e:
        return e.fields
    return ()


def unsupported_credential_keys_message(provider_name, keys):
    one = len(keys) == 1
    return (
        f"Unsupported credential {'key' if one else 'keys'} for provider "
        f"'{provider_name}': {', '.join(keys)}. Provider settings belong outside "
        "credentials. Allowed credential keys: "
        f"{', '.join(ALLOWED_CREDENTIAL_KEYS)}."
    )


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


class ProviderBaseURLField(serializers.CharField):
    """An http(s) URL with a host, which may be a single label.

    Django's URLValidator wants a dotted domain, so it refused a provider on
    the Docker network or in the cluster (http://ollama:11434, a Kubernetes
    Service short name) that the gateway UI saves. Whether the host may be
    called is ``ensure_provider_base_url_allowed``'s decision, in the view.
    """

    # A plain string schema intentionally also represents the empty sentinel
    # for an operator default. A generated URI validator rejects that valid
    # payload before it reaches the server. Non-empty inputs are checked here.
    default_error_messages = {"invalid": "Enter a valid URL."}

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "help_text",
            "HTTP(S) URL with a host, or empty to use a configured provider endpoint.",
        )
        super().__init__(**kwargs)

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


class ProviderAPIFormatField(serializers.ChoiceField):
    def __init__(self, **kwargs):
        super().__init__(
            choices=PROVIDER_API_FORMATS,
            error_messages={
                "invalid_choice": PROVIDER_FORMAT_ERROR,
                "blank": PROVIDER_FORMAT_ERROR,
            },
            **kwargs,
        )


class ProviderCredentialsField(serializers.DictField):
    def to_internal_value(self, data):
        # Report unknown keys before validating their values, including a
        # misplaced numeric weight or an object-valued provider setting.
        if isinstance(data, dict):
            name = self.parent.initial_data.get("provider_name", "")
            keys = unsupported_credential_keys(name, data)
            if keys:
                raise serializers.ValidationError(
                    unsupported_credential_keys_message(name, keys)
                )
        return super().to_internal_value(data)


class AgentccProviderCredentialCreateSerializer(serializers.Serializer):
    provider_name = serializers.CharField(max_length=100)
    display_name = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    credentials = ProviderCredentialsField(
        child=serializers.CharField(allow_blank=True)
    )
    base_url = ProviderBaseURLField(
        max_length=500, required=False, default="", allow_blank=True
    )
    api_format = ProviderAPIFormatField(required=False, default="openai")
    models_list = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    default_timeout_seconds = serializers.IntegerField(required=False, default=60)
    max_concurrent = serializers.IntegerField(required=False, default=100)
    conn_pool_size = serializers.IntegerField(required=False, default=100)
    extra_config = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False, default=dict
    )

    def validate_credentials(self, value):
        if not value:
            raise serializers.ValidationError("credentials must not be empty")
        if "api_key" not in value:
            raise serializers.ValidationError(
                "credentials must contain at least an 'api_key' field"
            )
        if not value["api_key"]:
            raise serializers.ValidationError("credentials.api_key must not be blank")
        return value

    def validate(self, attrs):
        try:
            ensure_provider_configuration(
                attrs["provider_name"], attrs["api_format"], attrs["base_url"]
            )
        except ValueError as e:
            raise serializers.ValidationError({"base_url": [str(e)]}) from e
        return attrs


class AgentccProviderCredentialUpdateSerializer(serializers.Serializer):
    """Partial update serializer — credentials can be rotated via the rotate action."""

    display_name = serializers.CharField(max_length=255, required=False)
    base_url = ProviderBaseURLField(max_length=500, required=False, allow_blank=True)
    api_format = ProviderAPIFormatField(required=False)
    models_list = serializers.ListField(child=serializers.CharField(), required=False)
    default_timeout_seconds = serializers.IntegerField(required=False)
    max_concurrent = serializers.IntegerField(required=False)
    conn_pool_size = serializers.IntegerField(required=False)
    extra_config = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if self.instance is not None:
            try:
                ensure_provider_configuration(
                    self.instance.provider_name,
                    attrs.get("api_format", self.instance.api_format),
                    attrs.get("base_url", self.instance.base_url),
                )
            except ValueError as e:
                raise serializers.ValidationError({"base_url": [str(e)]}) from e
        return attrs
