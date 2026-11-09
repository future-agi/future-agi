import logging

from rest_framework import serializers

from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.services.alert_dispatcher import threshold_errors
from agentcc.services.email_sender import public_error
from agentcc.services.secret_mask import MASKED_SECRET
from agentcc.services.url_safety import ensure_smtp_host_allowed
from integrations.services.credentials import CredentialManager

logger = logging.getLogger(__name__)

VALID_EVENTS = [e[0] for e in AgentccEmailAlert.EVENT_CHOICES]
VALID_PROVIDERS = [p[0] for p in AgentccEmailAlert.PROVIDER_CHOICES]

# Never read back: a read shows MASKED_SECRET in their place.
SECRET_CONFIG_KEYS = ("api_key", "password")
# What a provider authenticates with; none of them outlives a provider change.
CREDENTIAL_CONFIG_KEYS = ("api_key", "username", "password")
SMTP_CREDENTIAL_KEYS = ("username", "password")
DEFAULT_SMTP_PORT = 587
SMTP_CREDENTIALS_REQUIRED = (
    "Enter the SMTP username and password again: saved SMTP credentials are "
    "only sent to the host and port they were saved for."
)


def _smtp_destination(config):
    """The (host, port) an SMTP config connects to, compared loosely: host
    case and a trailing dot, and a port as int or digits, make no difference."""
    host = str(config.get("host") or "").strip().rstrip(".").lower()
    port = config.get("port", DEFAULT_SMTP_PORT)
    if isinstance(port, str) and port.strip().isdigit():
        port = int(port.strip())
    return host, port


class AgentccEmailAlertSerializer(serializers.ModelSerializer):
    provider_config = serializers.SerializerMethodField()

    class Meta:
        model = AgentccEmailAlert
        fields = [
            "id",
            "organization",
            "name",
            "recipients",
            "events",
            "thresholds",
            "provider",
            "provider_config",
            "is_active",
            "cooldown_minutes",
            "last_triggered_at",
            "last_error",
            "last_error_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "last_triggered_at",
            "last_error",
            "last_error_at",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        """last_error as a fixed message (email_sender.public_error): a value
        saved before ALERT-ACCESS-1 round 2 can hold the provider's or the
        mail server's own text, which can quote a saved password or API key."""
        data = super().to_representation(instance)
        if data.get("last_error"):
            data["last_error"] = public_error(data["last_error"])
        return data

    def get_provider_config(self, obj):
        """Return the config with each stored secret replaced by
        MASKED_SECRET: no character of it, nor its length, is read."""
        if not obj.encrypted_config:
            return {}
        try:
            config = CredentialManager.decrypt(bytes(obj.encrypted_config))
            return {
                key: MASKED_SECRET if key in SECRET_CONFIG_KEYS and value else value
                for key, value in config.items()
            }
        except Exception:
            logger.exception(
                "Failed to decrypt email alert config for alert %s", obj.id
            )
            return {}


class AgentccEmailAlertWriteSerializer(serializers.ModelSerializer):
    provider_config = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = AgentccEmailAlert
        fields = [
            "name",
            "recipients",
            "events",
            "thresholds",
            "provider",
            "provider_config",
            "is_active",
            "cooldown_minutes",
        ]

    def validate_recipients(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("recipients must be a JSON array")
        for email in value:
            if not isinstance(email, str) or "@" not in email:
                raise serializers.ValidationError(f"Invalid email: {email}")
        return value

    def validate_events(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("events must be a JSON array")
        for event in value:
            if event not in VALID_EVENTS:
                raise serializers.ValidationError(
                    f"Invalid event '{event}'. Valid: {', '.join(VALID_EVENTS)}"
                )
        return value

    def validate_thresholds(self, value):
        """The dispatcher's threshold keys must be valid numbers (other keys are
        stored as sent and never read)."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("thresholds must be a JSON object")
        errors = threshold_errors(value)
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def validate_provider(self, value):
        if value not in VALID_PROVIDERS:
            raise serializers.ValidationError(
                f"Invalid provider '{value}'. Valid: {', '.join(VALID_PROVIDERS)}"
            )
        return value

    def validate_provider_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("provider_config must be a JSON object")
        # A masked secret sent back from a read stands for "unchanged".
        return {
            key: item
            for key, item in value.items()
            if not (key in SECRET_CONFIG_KEYS and item == MASKED_SECRET)
        }

    def validate(self, attrs):
        """Resolve the config this write stores (attrs["provider_config"]).

        A secret goes only where it was saved for: a provider change drops the
        saved credentials, and an SMTP host or port change takes the username
        and password again. An SMTP host is checked unless it is the saved one,
        so an alert saved earlier stays editable.
        """
        attrs = super().validate(attrs)
        instance = self.instance
        provider = attrs.get("provider") or (
            instance.provider
            if instance is not None
            else AgentccEmailAlert._meta.get_field("provider").get_default()
        )
        incoming = attrs.get("provider_config")
        stored = None
        if instance is None:
            config = incoming or {}
        else:
            if incoming is None and provider == instance.provider:
                return attrs  # the stored config is untouched
            stored = self._stored_config(instance)
            config = self._merge(stored, incoming or {}, instance.provider, provider)
            if provider != instance.provider:
                stored = None  # the old provider's destination is not this one's

        if provider == "smtp":
            self._check_smtp_host(config, stored)
        attrs["provider_config"] = config
        return attrs

    @staticmethod
    def _stored_config(instance):
        if not instance.encrypted_config:
            return {}
        return CredentialManager.decrypt(bytes(instance.encrypted_config))

    @staticmethod
    def _merge(stored, incoming, old_provider, new_provider):
        if new_provider != old_provider:
            kept = {
                key: value
                for key, value in stored.items()
                if key not in CREDENTIAL_CONFIG_KEYS
            }
            return {**kept, **incoming}
        merged = {**stored, **incoming}
        if new_provider == "smtp" and _smtp_destination(merged) != _smtp_destination(
            stored
        ):
            held = [key for key in SMTP_CREDENTIAL_KEYS if stored.get(key)]
            if any(key not in incoming for key in held):
                raise serializers.ValidationError(
                    {"provider_config": SMTP_CREDENTIALS_REQUIRED}
                )
        return merged

    @staticmethod
    def _check_smtp_host(config, stored):
        if not config.get("host"):
            return  # nothing to connect to yet; a send says so
        if stored is not None and _smtp_destination(config) == _smtp_destination(
            stored
        ):
            return
        try:
            ensure_smtp_host_allowed(
                config["host"], config.get("port", DEFAULT_SMTP_PORT)
            )
        except ValueError as exc:
            raise serializers.ValidationError({"provider_config": str(exc)}) from exc

    def create(self, validated_data):
        provider_config = validated_data.pop("provider_config", {})
        instance = AgentccEmailAlert.no_workspace_objects.create(**validated_data)
        if provider_config:
            instance.encrypted_config = CredentialManager.encrypt(provider_config)
            instance.save(update_fields=["encrypted_config"])
        return instance

    def update(self, instance, validated_data):
        # validate() resolved the whole config to store, saved values merged.
        provider_config = validated_data.pop("provider_config", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if provider_config is not None:
            instance.encrypted_config = CredentialManager.encrypt(provider_config)
        instance.save()
        return instance


class AgentccEmailAlertTestSerializer(serializers.Serializer):
    recipient_override = serializers.EmailField(required=False)
