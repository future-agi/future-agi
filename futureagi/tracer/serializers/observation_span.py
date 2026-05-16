from rest_framework import serializers

from tracer.constants.provider_logos import PROVIDER_LOGOS
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.filters import filter_list_field
from tracer.utils.helper import validate_filters_helper


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
