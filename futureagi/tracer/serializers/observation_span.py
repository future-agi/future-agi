import json

from rest_framework import serializers

from tracer.constants.provider_logos import PROVIDER_LOGOS
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.filters import (
    StrictInputSerializer,
    filter_list_field,
    filter_list_query_param_field,
)
from tracer.utils.helper import validate_filters_helper


class ProjectScopeQueryParamField(serializers.CharField):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "description": 'JSON-encoded object with canonical key: {"project_id": "<uuid>"}.',
        }

    def to_internal_value(self, data):
        value = data
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    "filters must be valid JSON."
                ) from exc
        if not isinstance(value, dict):
            raise serializers.ValidationError("filters must be an object.")
        extra_keys = sorted(set(value) - {"project_id"})
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown filter keys: {', '.join(extra_keys)}"
            )
        project_id = value.get("project_id")
        if not project_id:
            raise serializers.ValidationError("project_id is required.")
        return {"project_id": str(serializers.UUIDField().run_validation(project_id))}


class ObservationAttributeListQuerySerializer(serializers.Serializer):
    filters = ProjectScopeQueryParamField()
    row_type = serializers.ChoiceField(
        choices=["spans", "traces", "sessions", "voiceCalls"],
        required=False,
        default="spans",
    )


class ObservationSpanSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )
    trace = serializers.PrimaryKeyRelatedField(queryset=Trace.objects.all(), many=False)
    project_version = serializers.PrimaryKeyRelatedField(
        queryset=ProjectVersion.objects.all(), many=False, required=False
    )
    provider_logo = serializers.SerializerMethodField()
    span_attributes = serializers.SerializerMethodField()

    class Meta:
        model = ObservationSpan
        fields = [
            "id",
            "project",
            "project_version",
            "trace",
            "parent_span_id",
            "name",
            "observation_type",
            "start_time",
            "end_time",
            "input",
            "output",
            "model",
            "model_parameters",
            "latency_ms",
            "org_id",
            "org_user_id",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "response_time",
            "eval_id",
            "cost",
            "status",
            "status_message",
            "tags",
            "metadata",
            "span_events",
            "provider",
            "provider_logo",
            "span_attributes",
            "custom_eval_config",
            "eval_status",
            "prompt_version",
        ]
        read_only_fields = ["provider_logo", "span_attributes"]

    def get_provider_logo(self, obj):
        provider = obj.provider
        if provider:
            return PROVIDER_LOGOS.get(provider.lower())
        return None

    def get_span_attributes(self, obj):
        """
        Return span_attributes as the canonical source.
        Falls back to eval_attributes for old data.
        """
        if obj.span_attributes and obj.span_attributes != {}:
            return obj.span_attributes
        return obj.eval_attributes or {}


class SpanExportSerializer(serializers.Serializer):
    filters = filter_list_field(required=False, default=[])

    def validate_filters(self, value):
        return validate_filters_helper(value)


class SpanObserveListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    user_id = serializers.CharField(required=False, allow_blank=True)
    filters = filter_list_query_param_field(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class SpanIndexQuerySerializer(StrictInputSerializer):
    span_id = serializers.CharField()
    project_version_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class SpanObserveIndexQuerySerializer(StrictInputSerializer):
    span_id = serializers.CharField()
    project_id = serializers.UUIDField()
    user_id = serializers.CharField(required=False, allow_blank=True)
    filters = filter_list_query_param_field(required=False, default=list)


class SubmitFeedbackActionTypeSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField(required=True)
    action_type = serializers.ChoiceField(
        choices=["retune", "recalculate"], required=True
    )
    custom_eval_config_id = serializers.UUIDField(required=True)
    feedback_id = serializers.UUIDField(required=True)


class SubmitFeedbackSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField(required=True)
    custom_eval_config_id = serializers.UUIDField(required=True)
    feedback_value = serializers.CharField(required=True)
    feedback_explanation = serializers.CharField(
        required=False, max_length=5000, allow_blank=True
    )
    feedback_improvement = serializers.CharField(
        required=False, max_length=5000, allow_blank=True
    )
