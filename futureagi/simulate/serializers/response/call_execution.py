from rest_framework import serializers


class CallLogEntryResponseSerializer(serializers.Serializer):
    """Nested serializer for a single call log entry."""

    id = serializers.CharField(read_only=True)
    logged_at = serializers.CharField(read_only=True, allow_null=True)
    level = serializers.CharField(read_only=True, allow_null=True)
    severity_text = serializers.CharField(read_only=True, allow_null=True)
    category = serializers.CharField(read_only=True, allow_null=True)
    body = serializers.CharField(read_only=True, allow_null=True)
    attributes = serializers.DictField(read_only=True, allow_null=True)
    payload = serializers.DictField(read_only=True, allow_null=True)


class CallExecutionLogsResponseSerializer(serializers.Serializer):
    """Inner dict typed by this serializer; paginator wraps in count/next/previous/results."""

    results = CallLogEntryResponseSerializer(many=True, read_only=True)
    source = serializers.CharField(read_only=True)
    ingestion_pending = serializers.BooleanField(read_only=True)


class CallExecutionDeleteResponseSerializer(serializers.Serializer):
    """Response serializer for DELETE /call-executions/{id}/"""

    message = serializers.CharField(read_only=True)


class CallExecutionErrorResponseSerializer(serializers.Serializer):
    """Standard error shape for call-execution endpoints."""

    error = serializers.CharField(read_only=True)
    details = serializers.DictField(required=False, read_only=True)
