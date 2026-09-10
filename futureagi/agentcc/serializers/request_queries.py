"""Public filters for gateway request logs and overview analytics."""

from rest_framework import serializers


class GatewayOverviewQuerySerializer(serializers.Serializer):
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)
    granularity = serializers.CharField(required=False)
    api_key_id = serializers.UUIDField(required=False)


class GatewayRequestLogQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)
    user_id = serializers.CharField(required=False, allow_blank=True)
    session_id = serializers.CharField(required=False, allow_blank=True)
    api_key_id = serializers.UUIDField(required=False)
    request_id = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(
        required=False, help_text="Comma-separated model names."
    )
    provider = serializers.CharField(
        required=False, help_text="Comma-separated provider names."
    )
    status_code = serializers.CharField(
        required=False, help_text="Comma-separated HTTP status codes."
    )
    min_status_code = serializers.IntegerField(required=False)
    max_status_code = serializers.IntegerField(required=False)
    is_error = serializers.BooleanField(required=False)
    cache_hit = serializers.BooleanField(required=False)
    fallback_used = serializers.BooleanField(required=False)
    guardrail_triggered = serializers.BooleanField(required=False)
    is_stream = serializers.BooleanField(required=False)
    started_after = serializers.DateTimeField(required=False)
    started_before = serializers.DateTimeField(required=False)
    min_latency = serializers.IntegerField(required=False)
    max_latency = serializers.IntegerField(required=False)
    min_cost = serializers.FloatField(required=False)
    max_cost = serializers.FloatField(required=False)
    min_tokens = serializers.IntegerField(required=False)
    max_tokens = serializers.IntegerField(required=False)
    q = serializers.CharField(required=False, allow_blank=True)
    search = serializers.CharField(required=False, allow_blank=True)
    ordering = serializers.CharField(required=False)
