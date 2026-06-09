from rest_framework import serializers

from simulate.models import CallExecution


class CallExecutionFilterSerializer(serializers.Serializer):
    """Query-parameter serializer for GET /call-executions/"""

    search = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.CharField(required=False, allow_blank=True, default="")
    test_execution_id = serializers.UUIDField(required=False, allow_null=True)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1)


class CallExecutionStatusUpdateSerializer(serializers.Serializer):
    """Request body serializer for PATCH /call-executions/{id}/"""

    status = serializers.ChoiceField(choices=CallExecution.CallStatus.choices)
    ended_reason = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )

    def validate_status(self, value):
        return value.lower() if isinstance(value, str) else value
