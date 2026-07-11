import logging

from rest_framework import serializers

from agentcc.models.email_alert import AgentccEmailAlert
from integrations.services.credentials import CredentialManager

logger = logging.getLogger(__name__)

VALID_EVENTS = [e[0] for e in AgentccEmailAlert.EVENT_CHOICES]
VALID_PROVIDERS = [p[0] for p in AgentccEmailAlert.PROVIDER_CHOICES]


class AgentccEmailAlertSerializer(serializers.ModelSerializer):
    """Email alert configuration that notifies recipients when gateway events
    fire (budget exceeded, error spikes, guardrail triggers, latency/cost
    thresholds). Use it to set up email notifications via SendGrid, Resend, or
    SMTP. Listed/read via list_agentcc_email_alerts / get_agentcc_email_alert;
    provider_config is returned with secrets masked."""

    provider_config = serializers.SerializerMethodField(
        help_text="Provider credentials/settings, returned with secrets masked (read-only)."
    )

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
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "last_triggered_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {"help_text": "Unique (per org) name for this alert."},
            "recipients": {"help_text": "JSON array of recipient email addresses."},
            "events": {
                "help_text": "JSON array of event types to alert on (e.g. budget.exceeded, error.rate_spike, guardrail.triggered)."
            },
            "thresholds": {
                "help_text": "JSON object of per-event threshold values that gate when an alert fires."
            },
            "provider": {"help_text": "Email provider: sendgrid, resend, or smtp."},
            "is_active": {"help_text": "Whether this alert is enabled."},
            "cooldown_minutes": {
                "help_text": "Minimum minutes between consecutive alerts of the same kind."
            },
            "last_triggered_at": {
                "help_text": "Timestamp the alert last fired (read-only)."
            },
        }

    def get_provider_config(self, obj):
        """Return masked config (hide sensitive values)."""
        if not obj.encrypted_config:
            return {}
        try:
            config = CredentialManager.decrypt(bytes(obj.encrypted_config))
            masked = {}
            for key, value in config.items():
                if key in ("api_key", "password") and value:
                    masked[key] = (
                        value[:4] + "..." + value[-4:] if len(value) > 8 else "****"
                    )
                else:
                    masked[key] = value
            return masked
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

    def validate_provider(self, value):
        if value not in VALID_PROVIDERS:
            raise serializers.ValidationError(
                f"Invalid provider '{value}'. Valid: {', '.join(VALID_PROVIDERS)}"
            )
        return value

    def validate_provider_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("provider_config must be a JSON object")
        return value

    def create(self, validated_data):
        provider_config = validated_data.pop("provider_config", {})
        instance = AgentccEmailAlert.no_workspace_objects.create(**validated_data)
        if provider_config:
            instance.encrypted_config = CredentialManager.encrypt(provider_config)
            instance.save(update_fields=["encrypted_config"])
        return instance

    def update(self, instance, validated_data):
        provider_config = validated_data.pop("provider_config", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if provider_config is not None:
            current_config = {}
            if instance.encrypted_config:
                current_config = CredentialManager.decrypt(
                    bytes(instance.encrypted_config)
                )
            instance.encrypted_config = CredentialManager.encrypt(
                {**current_config, **provider_config}
            )
        instance.save()
        return instance


class AgentccEmailAlertTestSerializer(serializers.Serializer):
    recipient_override = serializers.EmailField(required=False)
