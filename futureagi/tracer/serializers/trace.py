from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.helper import validate_filters_helper


class TraceSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )
    project_version = serializers.PrimaryKeyRelatedField(
        queryset=ProjectVersion.objects.all(), many=False, required=False
    )
    session = serializers.PrimaryKeyRelatedField(
        queryset=TraceSession.objects.all(), many=False, required=False
    )

    class Meta:
        model = Trace
        fields = [
            "id",
            "project",
            "project_version",
            "name",
            "metadata",
            "input",
            "output",
            "error",
            "session",
            "external_id",
            "tags",
        ]


class TraceExportSerializer(serializers.Serializer):
    filters = serializers.ListField(
        required=False, default=[], child=serializers.JSONField()
    )

    def validate_filters(self, value):
        return validate_filters_helper(value)


class UsersQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=500)
    current_page_index = serializers.IntegerField(required=False, min_value=0)
    sort_params = serializers.JSONField(required=False)
    filters = serializers.JSONField(required=False)


class UsersTableRowSerializer(serializers.Serializer):
    user_id = serializers.CharField(required=False, allow_null=True)
    total_cost = serializers.FloatField()
    total_tokens = serializers.IntegerField(required=False, allow_null=True)
    input_tokens = serializers.IntegerField(required=False, allow_null=True)
    output_tokens = serializers.IntegerField(required=False, allow_null=True)
    num_traces = serializers.IntegerField(required=False, allow_null=True)
    num_sessions = serializers.IntegerField(required=False, allow_null=True)
    avg_session_duration = serializers.FloatField(required=False, allow_null=True)
    avg_trace_latency = serializers.FloatField(required=False, allow_null=True)
    num_llm_calls = serializers.IntegerField(required=False, allow_null=True)
    num_guardrails_triggered = serializers.IntegerField(required=False, allow_null=True)
    activated_at = serializers.DateTimeField(required=False, allow_null=True)
    last_active = serializers.DateTimeField(required=False, allow_null=True)
    num_active_days = serializers.IntegerField(required=False, allow_null=True)
    num_traces_with_errors = serializers.IntegerField(required=False, allow_null=True)
    bool_eval_pass_rate = serializers.FloatField(required=False, allow_null=True)
    avg_output_float = serializers.FloatField(required=False, allow_null=True)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    user_id_type = serializers.CharField(required=False, allow_null=True)
    user_id_hash = serializers.CharField(required=False, allow_null=True)
    end_user_id = serializers.UUIDField(required=False, allow_null=True)


class UsersResultSerializer(serializers.Serializer):
    table = serializers.ListField(child=serializers.JSONField())
    total_count = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class UsersResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = UsersResultSerializer()


class UserCodeExampleResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()
