from rest_framework import serializers

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    ensure_public_http_url,
)
from agentcc.validators import validate_safe_agentcc_name

VALID_EVENTS = [e[0] for e in AgentccWebhook.EVENT_CHOICES]


class AgentccWebhookSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentccWebhook
        fields = [
            "id",
            "organization",
            "name",
            "url",
            "secret",
            "events",
            "is_active",
            "headers",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "secret": {"write_only": True},
        }

    def validate_events(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("events must be a JSON array")
        for event in value:
            if event not in VALID_EVENTS:
                raise serializers.ValidationError(
                    f"Invalid event '{event}'. Valid events: {', '.join(VALID_EVENTS)}"
                )
        return value

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_url(self, value):
        try:
            ensure_public_http_url(
                value, WEBHOOK_PRIVATE_URL_ERROR, allow_unresolvable=True
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return value


class AgentccWebhookEventSerializer(serializers.ModelSerializer):
    webhook_name = serializers.CharField(source="webhook.name", read_only=True)

    class Meta:
        model = AgentccWebhookEvent
        fields = [
            "id",
            "organization",
            "webhook",
            "webhook_name",
            "event_type",
            "payload",
            "status",
            "attempts",
            "max_attempts",
            "last_attempt_at",
            "last_response_code",
            "last_error",
            "next_retry_at",
            "created_at",
        ]
        read_only_fields = fields
