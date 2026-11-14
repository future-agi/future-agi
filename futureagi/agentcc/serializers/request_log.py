from rest_framework import serializers

from agentcc.models import AgentccRequestLog


class AgentccRequestLogSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — excludes large body fields."""

    class Meta:
        model = AgentccRequestLog
        fields = [
            "id",
            "request_id",
            "model",
            "provider",
            "resolved_model",
            "latency_ms",
            "started_at",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost",
            "status_code",
            "is_stream",
            "is_error",
            "error_message",
            "cache_hit",
            "fallback_used",
            "guardrail_triggered",
            "api_key_id",
            "user_id",
            "session_id",
            "routing_strategy",
            "metadata",
            "organization",
            "workspace",
            "created_at",
        ]
        read_only_fields = fields


class AgentccRequestLogDetailSerializer(serializers.ModelSerializer):
    """Full serializer for detail views — includes request/response bodies."""

    class Meta:
        model = AgentccRequestLog
        fields = [
            "id",
            "request_id",
            "model",
            "provider",
            "resolved_model",
            "latency_ms",
            "started_at",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost",
            "status_code",
            "is_stream",
            "is_error",
            "error_message",
            "cache_hit",
            "fallback_used",
            "guardrail_triggered",
            "api_key_id",
            "user_id",
            "session_id",
            "routing_strategy",
            "metadata",
            "request_body",
            "response_body",
            "request_headers",
            "response_headers",
            "guardrail_results",
            "organization",
            "workspace",
            "created_at",
        ]
        read_only_fields = fields


class AgentccSessionSerializer(serializers.Serializer):
    """Serializer for session aggregation results."""

    session_id = serializers.CharField()
    request_count = serializers.IntegerField()
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=6)
    total_tokens = serializers.IntegerField()
    avg_latency = serializers.FloatField()
    first_request_at = serializers.DateTimeField()
    last_request_at = serializers.DateTimeField()
    error_count = serializers.IntegerField()
    models = serializers.ListField(child=serializers.CharField())
    providers = serializers.ListField(child=serializers.CharField())


class AgentccRequestLogMetadataValuesSerializer(serializers.Serializer):
    """Options for the Application, Service and Custom Tags filters."""

    application = serializers.ListField(child=serializers.CharField())
    service = serializers.ListField(child=serializers.CharField())
    tags = serializers.ListField(
        child=serializers.CharField(),
        help_text="key:value pairs, for keys declared as custom properties.",
    )


class AgentccRequestLogMetadataValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AgentccRequestLogMetadataValuesSerializer()
