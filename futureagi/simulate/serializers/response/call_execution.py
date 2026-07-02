from rest_framework import serializers

from model_hub.models.error_localizer_model import ErrorLocalizerStatus
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer

_EL_TASK_FE_STATUS = {
    ErrorLocalizerStatus.RUNNING: "running",
    ErrorLocalizerStatus.COMPLETED: "completed",
    ErrorLocalizerStatus.FAILED: "failed",
}


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


class ErrorLocalizerTaskResponseSerializer(serializers.Serializer):
    task_id = serializers.UUIDField(read_only=True)
    eval_config_id = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True, allow_blank=True)
    eval_result = serializers.JSONField(read_only=True, allow_null=True)
    eval_explanation = serializers.CharField(read_only=True, allow_null=True)
    input_data = serializers.JSONField(read_only=True, allow_null=True)
    input_keys = serializers.JSONField(read_only=True, allow_null=True)
    input_types = serializers.JSONField(read_only=True, allow_null=True)
    rule_prompt = serializers.CharField(read_only=True, allow_null=True)
    error_analysis = serializers.JSONField(read_only=True, allow_null=True)
    selected_input_key = serializers.CharField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True, allow_null=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    eval_template_name = serializers.CharField(read_only=True, allow_null=True)
    eval_template_id = serializers.UUIDField(read_only=True, allow_null=True)

    def to_representation(self, instance):
        data = dict(instance) if isinstance(instance, dict) else super().to_representation(instance)
        data["status"] = _EL_TASK_FE_STATUS.get(data.get("status"), "")
        return data


class CallExecutionErrorLocalizerTasksResponseSerializer(serializers.Serializer):
    """Response for GET /simulate/call-executions/{id}/error-localizer-tasks/."""

    call_execution_id = serializers.UUIDField(read_only=True)
    error_localizer_tasks = ErrorLocalizerTaskResponseSerializer(
        many=True, read_only=True
    )
    total_tasks = serializers.IntegerField(read_only=True)


class SessionComparisonResultSerializer(serializers.Serializer):
    """Result payload for chat or voice replay session comparison."""

    comparison_metrics = serializers.JSONField(read_only=True)
    comparison_transcripts = serializers.JSONField(read_only=True)
    comparison_recordings = serializers.JSONField(
        read_only=True, required=False, allow_null=True
    )


class SessionComparisonResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SessionComparisonResultSerializer()


class CallExecutionErrorResponseSerializer(ApiTextErrorResponseSerializer):
    """Standard error shape for call-execution endpoints."""
