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
            "consecutive_failures",
            "disabled_reason",
            "disabled_at",
            "dropped_events_total",
            "last_dropped_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "consecutive_failures",
            "disabled_reason",
            "disabled_at",
            "dropped_events_total",
            "last_dropped_at",
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

    def validate_headers(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "headers must be a JSON object of header names to values"
            )
        for name, header_value in value.items():
            if not isinstance(header_value, str | int | float) or isinstance(
                header_value, bool
            ):
                raise serializers.ValidationError(
                    f"Header '{name}' must have a text or number value"
                )
        return {name: str(header_value) for name, header_value in value.items()}

    def update(self, instance, validated_data):
        # Write only what the request set. ModelSerializer.update saves every
        # column of the copy read at the start of the request, which would
        # put back health counters a delivery run changed meanwhile.
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save(update_fields=[*validated_data, "updated_at"])
        return instance

    def validate_name(self, value):
        try:
            return validate_safe_agentcc_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_url(self, value):
        try:
            ensure_public_http_url(value, WEBHOOK_PRIVATE_URL_ERROR)
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
