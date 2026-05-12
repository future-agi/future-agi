from rest_framework import serializers


class ErrorResponseSerializer(serializers.Serializer):
    """Docs-only. Used in @swagger_auto_schema error responses."""

    error = serializers.CharField()


class CancelTestExecutionResponseSerializer(serializers.Serializer):
    """Response serializer for POST /test-executions/{test_execution_id}/cancel/"""

    success = serializers.BooleanField()
    message = serializers.CharField()
    test_execution_id = serializers.UUIDField(allow_null=True)


class FailedRerunItemSerializer(serializers.Serializer):
    """A single failed rerun entry."""

    call_execution_id = serializers.UUIDField()
    error = serializers.CharField()


class RerunCallsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /test-executions/{test_execution_id}/rerun-calls/"""

    message = serializers.CharField()
    test_execution_id = serializers.UUIDField()
    rerun_type = serializers.CharField()
    total_processed = serializers.IntegerField()
    successful_reruns = serializers.ListField(child=serializers.UUIDField())
    failed_reruns = FailedRerunItemSerializer(many=True)
    success_count = serializers.IntegerField()
    failure_count = serializers.IntegerField()
