from rest_framework import serializers

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    ensure_public_http_url,
)
from agentcc.validators import validate_safe_agentcc_name

VALID_EVENTS = [e[0] for e in AgentccWebhook.EVENT_CHOICES]


class AgentccWebhookSerializer(serializers.ModelSerializer):
    """An outbound webhook endpoint that the gateway POSTs to when subscribed
    events fire (request.completed, guardrail.triggered, budget.exceeded, etc.).
    Use it to push gateway events to your own systems. Listed/read via
    list_agentcc_webhooks / get_agentcc_webhook; the signing secret is
    write-only and never returned."""

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
            "secret": {
                "write_only": True,
                "help_text": "Signing secret used to compute the webhook signature (write-only).",
            },
            "name": {"help_text": "Unique (per org) name for this webhook."},
            "url": {
                "help_text": "Public HTTPS URL the gateway POSTs events to; private/internal URLs are rejected."
            },
            "events": {
                "help_text": "JSON array of event types to subscribe to (e.g. request.completed, guardrail.triggered, budget.exceeded)."
            },
            "is_active": {"help_text": "Whether deliveries to this webhook are enabled."},
            "headers": {
                "help_text": "JSON object of extra HTTP headers to send with each delivery."
            },
            "description": {"help_text": "Optional description of the webhook."},
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
            ensure_public_http_url(value, WEBHOOK_PRIVATE_URL_ERROR)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return value


class AgentccWebhookEventSerializer(serializers.ModelSerializer):
    """A single delivery record for an outbound webhook — captures the event
    payload, delivery status (pending/delivered/failed/dead_letter), retry
    attempts, and the last response/error. Read-only; use it to inspect webhook
    delivery history. Listed/read via list_agentcc_webhook_events /
    get_agentcc_webhook_event."""

    webhook_name = serializers.CharField(
        source="webhook.name",
        read_only=True,
        help_text="Name of the webhook this event was delivered to (read-only).",
    )

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
        extra_kwargs = {
            "webhook": {
                "help_text": "UUID of the webhook this event belongs to (from list_agentcc_webhooks)."
            },
            "event_type": {
                "help_text": "Event type that triggered this delivery (e.g. request.completed)."
            },
            "payload": {"help_text": "JSON payload that was/will be sent."},
            "status": {
                "help_text": "Delivery state: pending, delivered, failed, or dead_letter."
            },
            "attempts": {"help_text": "Number of delivery attempts made so far."},
            "max_attempts": {
                "help_text": "Maximum delivery attempts before giving up (dead_letter)."
            },
            "last_attempt_at": {
                "help_text": "Timestamp of the most recent delivery attempt."
            },
            "last_response_code": {
                "help_text": "HTTP status code from the last attempt, if any."
            },
            "last_error": {
                "help_text": "Error message from the last failed attempt, if any."
            },
            "next_retry_at": {
                "help_text": "Scheduled time of the next retry, if pending."
            },
        }
