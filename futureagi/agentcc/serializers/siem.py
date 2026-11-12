"""The SIEM export API's contract (G16-SIEM): request bodies validated at
runtime and the response shapes the console reads (validated_request)."""

from rest_framework import serializers

from agentcc.models.siem import STREAMS, AgentccSIEMDelivery, AgentccSIEMDestination

KINDS = AgentccSIEMDestination.Kind.values
STATUSES = AgentccSIEMDelivery.Status.values


class HECTokenMixin(serializers.Serializer):
    token = serializers.CharField(
        max_length=1024,
        required=False,
        write_only=True,
        trim_whitespace=False,
        help_text="HEC write-only token; never returned.",
    )


class SIEMConnectionTestRequestSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)
    token = serializers.CharField(
        max_length=1024, write_only=True, trim_whitespace=False
    )


class SIEMConnectionTestResultSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    message = serializers.CharField()


class SIEMConnectionTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMConnectionTestResultSerializer()


class SIEMDestinationCreateRequestSerializer(HECTokenMixin):
    name = serializers.CharField(max_length=120)
    kind = serializers.ChoiceField(
        choices=KINDS,
        help_text="webhook: a Standard Webhooks-signed HTTPS endpoint; otlp_http: "
        "an OTLP/HTTP collector's base URL (JSON encoding); hec: an HTTP Event Collector endpoint.",
    )
    url = serializers.CharField(max_length=2048)
    streams = serializers.ListField(
        child=serializers.ChoiceField(choices=STREAMS), min_length=1, max_length=2
    )
    auth_header = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text="otlp_http only: the header that carries auth_value.",
    )
    auth_value = serializers.CharField(
        max_length=1024,
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="otlp_http only: the auth header's value. Write-only.",
    )


class SIEMDestinationUpdateRequestSerializer(HECTokenMixin):
    name = serializers.CharField(max_length=120, required=False)
    url = serializers.CharField(max_length=2048, required=False)
    streams = serializers.ListField(
        child=serializers.ChoiceField(choices=STREAMS),
        min_length=1,
        max_length=2,
        required=False,
    )
    enabled = serializers.BooleanField(
        required=False,
        help_text="Turning a destination on needs the deployment's SIEM export "
        "switch; both directions are audited.",
    )
    auth_header = serializers.CharField(max_length=64, required=False, allow_blank=True)
    auth_value = serializers.CharField(
        max_length=1024, required=False, allow_blank=True, write_only=True
    )


class SIEMRotateSecretRequestSerializer(HECTokenMixin):
    auth_value = serializers.CharField(
        max_length=1024,
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="otlp_http only: the new auth header value. A webhook gets a "
        "new signing secret instead.",
    )


class SIEMDestinationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=KINDS)
    url = serializers.CharField()
    streams = serializers.ListField(child=serializers.ChoiceField(choices=STREAMS))
    enabled = serializers.BooleanField()
    auth_header = serializers.CharField(allow_blank=True)
    has_secret = serializers.BooleanField()
    secret_hint = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    last_delivered_at = serializers.DateTimeField(allow_null=True)
    last_failed_at = serializers.DateTimeField(allow_null=True)
    pending_count = serializers.IntegerField()
    dead_letter_count = serializers.IntegerField()


class SIEMDestinationWithSecretSerializer(SIEMDestinationSerializer):
    signing_secret = serializers.CharField(
        required=False,
        help_text="A webhook's new signing secret (whsec_ + base64), in this "
        "answer only.",
    )


class SIEMDestinationListResultSerializer(serializers.Serializer):
    can_manage = serializers.BooleanField()
    export_available = serializers.BooleanField(
        help_text="Whether this deployment's SIEM export switch is on."
    )
    destinations = SIEMDestinationSerializer(many=True)


class SIEMDestinationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDestinationListResultSerializer()


class SIEMDestinationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDestinationSerializer()


class SIEMDestinationSecretResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDestinationWithSecretSerializer()


class SIEMDestinationDeletedResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()


class SIEMDestinationDeletedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDestinationDeletedResultSerializer()


class SIEMDeliverySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    destination_id = serializers.UUIDField()
    destination_name = serializers.CharField()
    stream = serializers.ChoiceField(choices=STREAMS)
    event_count = serializers.IntegerField()
    status = serializers.ChoiceField(choices=STATUSES)
    attempts = serializers.IntegerField()
    max_attempts = serializers.IntegerField()
    last_response_code = serializers.IntegerField(allow_null=True)
    last_error = serializers.CharField(
        allow_blank=True,
        help_text="A status line or an error class only, never a response body.",
    )
    last_attempt_at = serializers.DateTimeField(allow_null=True)
    next_retry_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    delivered_at = serializers.DateTimeField(allow_null=True)


class SIEMDeliveryListQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=STATUSES, required=False)
    destination_id = serializers.UUIDField(required=False)


class SIEMDeliveryListResultSerializer(serializers.Serializer):
    deliveries = SIEMDeliverySerializer(many=True)


class SIEMDeliveryListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDeliveryListResultSerializer()


class SIEMDeliveryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SIEMDeliverySerializer()
