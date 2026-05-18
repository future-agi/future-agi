from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import (
    filter_list_field,
    filter_list_query_param_field,
    parse_filter_list_payload,
)
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
    filters = filter_list_field(required=False, default=[])

    def validate_filters(self, value):
        return validate_filters_helper(value)


class CommaSeparatedStringListField(serializers.Field):
    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, (list, tuple)):
            items = data
        else:
            items = str(data).split(",")
        return [str(item).strip() for item in items if str(item).strip()]

    def to_representation(self, value):
        return value or []


class SortParamField(serializers.JSONField):
    ALLOWED_KEYS = {"column_id", "direction"}
    REQUIRED_KEYS = {"column_id"}

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Sort item must be an object.")
        missing = sorted(self.REQUIRED_KEYS - set(value))
        if missing:
            raise serializers.ValidationError(
                f"Missing sort item keys: {', '.join(missing)}"
            )
        extra = sorted(set(value) - self.ALLOWED_KEYS)
        if extra:
            raise serializers.ValidationError(
                f"Unknown sort item keys: {', '.join(extra)}"
            )
        direction = value.get("direction", "desc")
        if direction not in ("asc", "desc"):
            raise serializers.ValidationError("direction must be 'asc' or 'desc'.")
        return {"column_id": value["column_id"], "direction": direction}


class SortParamListQueryParamField(serializers.CharField):
    def to_internal_value(self, data):
        sort_params = parse_filter_list_payload(data)
        return serializers.ListField(child=SortParamField()).run_validation(sort_params)


class TraceListQuerySerializer(serializers.Serializer):
    project_version_id = serializers.UUIDField(required=True)
    trace_ids = CommaSeparatedStringListField(required=False, default=list)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class TraceAgentGraphQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class UsersQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=500)
    current_page_index = serializers.IntegerField(required=False, min_value=0)
    sort_params = serializers.JSONField(required=False)
    filters = filter_list_query_param_field(required=False, default=list)


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
