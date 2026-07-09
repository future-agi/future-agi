import json

from django.db.models import Q
from rest_framework import serializers

from tracer.constants.provider_logos import PROVIDER_LOGOS
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.filters import (
    StrictInputSerializer,
    filter_list_query_param_field,
)


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


class ObservationAttributeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.ListField(child=serializers.CharField())


class ObservationSpanSerializer(serializers.ModelSerializer):
    """A single observation span — one unit of work inside a trace (an LLM call, tool call, retriever, chain, agent step, etc.). Spans carry the I/O, model, token/cost/latency telemetry and OTel attributes for that step; many spans (linked via parent_span_id) make up a trace. List spans for a project version via list_spans and open one with get_span to see its full detail plus attached eval scores."""

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text="UUID of the trace project this span belongs to (from list_projects).",
    )
    trace = serializers.PrimaryKeyRelatedField(
        queryset=Trace.objects.all(),
        many=False,
        help_text="UUID of the trace this span is part of (from list_traces).",
    )
    project_version = serializers.PrimaryKeyRelatedField(
        queryset=ProjectVersion.objects.all(),
        many=False,
        required=False,
        help_text="UUID of the project version (experiment) this span was logged under, if any.",
    )
    provider_logo = serializers.SerializerMethodField(
        help_text="Logo URL for the span's LLM provider, when known (read-only)."
    )
    span_attributes = serializers.SerializerMethodField(
        help_text="Raw OTel span attributes captured for this span (read-only)."
    )

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
        extra_kwargs = {
            "id": {
                "help_text": "ID of the span; pass it to get_span to fetch this span's detail."
            },
            "parent_span_id": {
                "help_text": "ID of the parent span; null for the trace's root span. Used to reconstruct the span tree."
            },
            "name": {"help_text": "Human-readable name of the span/operation."},
            "observation_type": {
                "help_text": "Kind of work this span represents, e.g. llm, tool, chain, retriever, embedding, agent, reranker, guardrail, evaluator, conversation."
            },
            "start_time": {"help_text": "When the span started (ISO 8601)."},
            "end_time": {"help_text": "When the span ended (ISO 8601)."},
            "input": {"help_text": "Input payload for this span (e.g. the prompt/messages)."},
            "output": {"help_text": "Output payload produced by this span (e.g. the model response)."},
            "model": {"help_text": "Model name used by this span (for llm spans)."},
            "model_parameters": {
                "help_text": "Model invocation parameters (temperature, max_tokens, etc.)."
            },
            "latency_ms": {"help_text": "Span duration in milliseconds."},
            "prompt_tokens": {"help_text": "Number of input/prompt tokens consumed."},
            "completion_tokens": {"help_text": "Number of output/completion tokens generated."},
            "total_tokens": {"help_text": "Total tokens (prompt + completion) for this span."},
            "response_time": {"help_text": "Time to first/last response, in seconds, when recorded."},
            "cost": {"help_text": "Estimated cost of this span in USD."},
            "status": {
                "help_text": "OTel span status: UNSET, OK, or ERROR."
            },
            "status_message": {"help_text": "Status/error message attached to the span, if any."},
            "tags": {"help_text": "List of tags attached to the span."},
            "metadata": {"help_text": "Arbitrary user-supplied metadata for the span."},
            "span_events": {"help_text": "List of OTel span events recorded during the span."},
            "provider": {"help_text": "LLM provider for this span (e.g. openai, anthropic), when known."},
            "custom_eval_config": {
                "help_text": "UUID of the custom eval config associated with this span, if it was created by an evaluator (from list_custom_eval_configs)."
            },
            "eval_status": {
                "help_text": "Denormalized evaluation status flag for the span (note: derived/snapshot, may be stale)."
            },
            "prompt_version": {
                "help_text": "UUID of the prompt version linked to this span, if any."
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if not request:
            return

        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        if not organization:
            return

        workspace = getattr(request, "workspace", None)
        project_scope = Q(organization=organization)
        related_project_scope = Q(project__organization=organization)

        if workspace:
            if getattr(workspace, "is_default", False):
                project_workspace_scope = (
                    Q(workspace=workspace)
                    | Q(workspace__is_default=True, workspace__organization=organization)
                    | Q(workspace__isnull=True)
                )
                related_workspace_scope = (
                    Q(project__workspace=workspace)
                    | Q(
                        project__workspace__is_default=True,
                        project__workspace__organization=organization,
                    )
                    | Q(project__workspace__isnull=True)
                )
            else:
                project_workspace_scope = Q(workspace=workspace)
                related_workspace_scope = Q(project__workspace=workspace)

            project_scope &= project_workspace_scope
            related_project_scope &= related_workspace_scope

        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        trace_manager = getattr(Trace, "no_workspace_objects", Trace.objects)
        version_manager = getattr(
            ProjectVersion, "no_workspace_objects", ProjectVersion.objects
        )

        self.fields["project"].queryset = project_manager.filter(
            project_scope, deleted=False
        )
        self.fields["trace"].queryset = trace_manager.filter(
            related_project_scope, deleted=False
        )
        self.fields["project_version"].queryset = version_manager.filter(
            related_project_scope, deleted=False
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        project = attrs.get("project") or getattr(instance, "project", None)
        trace = attrs.get("trace") or getattr(instance, "trace", None)
        project_version = attrs.get("project_version") or getattr(
            instance, "project_version", None
        )

        if project and trace and trace.project_id != project.id:
            raise serializers.ValidationError(
                {"trace": "Trace must belong to the selected project."}
            )

        if project and project_version and project_version.project_id != project.id:
            raise serializers.ValidationError(
                {
                    "project_version": (
                        "Project version must belong to the selected project."
                    )
                }
            )

        return attrs

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


class SpanExportQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class SpanListQuerySerializer(StrictInputSerializer):
    project_version_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class SpanObserveListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
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
    observation_span_id = serializers.CharField(
        required=True,
        help_text=(
            "ID of the observation span the eval result belongs to "
            "(from list_spans)."
        ),
    )
    custom_eval_config_id = serializers.UUIDField(
        required=True,
        help_text=(
            "UUID of the eval config whose result is being rated "
            "(from get_trace_eval_names / list_custom_eval_configs)."
        ),
    )
    feedback_value = serializers.CharField(
        required=True,
        help_text="The agree/disagree feedback value for the eval result.",
    )
    feedback_explanation = serializers.CharField(
        required=False,
        max_length=5000,
        allow_blank=True,
        help_text="Optional free-text explanation of the feedback.",
    )
    feedback_improvement = serializers.CharField(
        required=False,
        max_length=5000,
        allow_blank=True,
        help_text="Optional note on how the eval output should improve.",
    )
