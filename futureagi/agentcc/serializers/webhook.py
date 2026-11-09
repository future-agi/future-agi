from rest_framework import serializers

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    ensure_public_http_url,
)
from agentcc.services.webhook_credential_access import masked_headers
from agentcc.services.webhook_credential_updates import (
    create_webhook,
    update_webhook,
)
from agentcc.services.webhook_delivery import shown_delivery_error
from agentcc.services.webhook_headers import UnsupportedHeaders, header_map
from agentcc.validators import validate_safe_agentcc_name

VALID_EVENTS = [e[0] for e in AgentccWebhook.EVENT_CHOICES]


class WebhookHeadersField(serializers.JSONField):
    """Normal JSON input; the only read value is the established masked DTO."""

    def get_attribute(self, instance):
        return instance

    def to_representation(self, instance):
        return masked_headers(instance)


class AgentccWebhookSerializer(serializers.ModelSerializer):
    # Match the former model-generated fields: omitted is preserved/defaulted,
    # null is invalid, secret is trimmed, blank allowed and length limited.
    secret = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        write_only=True,
    )
    headers = WebhookHeadersField(required=False)

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
            raise serializers.ValidationError(str(exc)) from exc

    def validate_url(self, value):
        try:
            ensure_public_http_url(value, WEBHOOK_PRIVATE_URL_ERROR)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_headers(self, value):
        """Keep existing object/pair/falsy normalization and JSON value semantics."""
        try:
            return header_map(value)
        except UnsupportedHeaders as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def create(self, validated_data):
        return create_webhook(validated_data)

    def update(self, instance, validated_data):
        return update_webhook(instance, validated_data)


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

    def to_representation(self, instance):
        """last_error as a fixed message (shown_delivery_error): a value saved
        before ALERT-ACCESS-1 round 2 can hold the receiver's answer or an
        exception's text, which can quote a header value."""
        data = super().to_representation(instance)
        if "last_error" in data:
            data["last_error"] = shown_delivery_error(data["last_error"])
        return data
