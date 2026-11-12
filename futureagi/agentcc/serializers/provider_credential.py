from urllib.parse import urlsplit

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.config_push import (
    UnsupportedProviderCredentialFields,
    _normalize_provider_credentials,
)
from agentcc.services.provider_public_projection import public_provider_models
from agentcc.services.provider_secret_policy import (
    CREDENTIAL_REDACTION,
    public_provider_settings,
    reject_secret_extra_config,
)
from agentcc.services.provider_url_auth import (
    public_provider_base_url,
    reject_provider_url_userinfo,
)
from agentcc.services.provider_validation import (
    ALLOWED_CREDENTIAL_KEYS,
    PROVIDER_API_FORMATS,
    PROVIDER_FORMAT_ERROR,
    ensure_provider_configuration,
)
from integrations.services.credentials import CredentialManager
from rest_framework import serializers
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

    @staticmethod
    def _decrypted_credentials(obj):
        try:
            data = CredentialManager.decrypt(bytes(obj.encrypted_credentials))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def get_credentials(self, obj):
        """Use one marker for every length/type, including empty legacy values."""
        data = self._decrypted_credentials(obj)
        return dict.fromkeys(data, CREDENTIAL_REDACTION) if data is not None else {}

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result["extra_config"] = public_provider_settings(instance.extra_config)
        result["base_url"] = public_provider_base_url(instance.base_url)
        context = {"prior_credentials": self.context.get("prior_model_credentials", ())}
        if "model_credentials" in self.context:
            context["credentials"] = self.context["model_credentials"]
        projected = public_provider_models(instance, **context)
        result["models_list"] = projected["models"]
        if projected["dropped_model_count"] or "code" in projected:
            result["dropped_model_count"] = projected["dropped_model_count"]
        for field in ("code", "model_warning"):
            if field in projected:
                result[field] = projected[field]
        return result


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
            reject_provider_url_userinfo(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None
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


class ProviderExtraConfigField(serializers.DictField):
    def to_internal_value(self, data):
        # Refuse by name before nested field errors could reflect input paths.
        try:
            reject_secret_extra_config(data)
        except ValueError as e:
            raise serializers.ValidationError(str(e)) from None
        return super().to_internal_value(data)


def allows_keyless_system_one(provider_name, api_format, base_url):
    """Only an explicit Generic System One endpoint may omit its API key.

    Match the gateway's vendor IDs and host suffixes, not the display name.
    URL safety (including private-network policy) is still checked by the view.
    """
    if api_format not in ("systemone", "typesafe") or not base_url:
        return False
    try:
        url = ProviderBaseURLField().run_validation(base_url)
    except serializers.ValidationError:
        return False
    host = urlsplit(url).hostname.lower().rstrip(".")
    name = provider_name.lower()
    if name.startswith(("typesafe", "perplexity", "openai")):
        return False
    return not any(
        host == vendor or host.endswith("." + vendor)
        for vendor in ("typesafe.ai", "perplexity.ai", "openai.com")
    )


class AgentccProviderCredentialCreateSerializer(serializers.Serializer):
    provider_name = serializers.CharField(max_length=100)
    display_name = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    credentials = ProviderCredentialsField(
        child=serializers.CharField(allow_blank=True), write_only=True
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
    extra_config = ProviderExtraConfigField(
        child=JsonValueField(allow_null=True), required=False, default=dict
    )

    def validate(self, attrs):
        try:
            ensure_provider_configuration(
                attrs["provider_name"], attrs["api_format"], attrs["base_url"]
            )
        except ValueError as e:
            raise serializers.ValidationError({"base_url": [str(e)]}) from e
        if (
            not allows_keyless_system_one(
                attrs["provider_name"], attrs["api_format"], attrs["base_url"]
            )
            and not attrs["credentials"].get("api_key", "").strip()
        ):
            raise serializers.ValidationError(
                {"credentials": "credentials must contain a non-empty 'api_key' field"}
            )
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
    extra_config = ProviderExtraConfigField(
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
            if self.instance.api_format in ("systemone", "typesafe"):
                credentials = CredentialManager.decrypt(
                    bytes(self.instance.encrypted_credentials)
                )
                api_key = credentials.get("api_key")
                if not isinstance(api_key, str) or not api_key.strip():
                    if not allows_keyless_system_one(
                        self.instance.provider_name,
                        attrs.get("api_format", self.instance.api_format),
                        attrs.get("base_url", self.instance.base_url),
                    ):
                        raise serializers.ValidationError(
                            "A keyless provider must remain a Generic System One endpoint "
                            "with a valid explicit base URL. Add an API key before changing it."
                        )
        return attrs
