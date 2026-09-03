import json

from django.db.models import Q
from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
from tracer.constants.provider_logos import PROVIDER_LOGOS
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.cursor_pagination import (
    CURSOR_HELP_TEXT,
    validate_cursor_exclusivity,
)
from tracer.serializers.filters import (
    BOUNDED_PAGE_NUMBER_HELP_TEXT,
    StrictInputSerializer,
    bounded_filter_list_query_param_field,
    filter_list_query_param_field,
)
from tracer.services.clickhouse.attribute_reads import validate_attribute_key


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
    q = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=512,
    )

    def validate_q(self, value):
        try:
            return validate_attribute_key(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ObservationAttributeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.ListField(child=serializers.CharField())
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=["complete", "sampled", "degraded"], required=False
    )
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField(required=False)
    query_window_end = serializers.DateTimeField(required=False)


class RootSpansQuerySerializer(serializers.Serializer):
    # Repeated query params: ?trace_ids=<id>&trace_ids=<id> (DRF ListField reads
    # QueryDict.getlist). CharField (not UUID): collector ids are hash strings.
    trace_ids = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    project_ids = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )


class RootSpansResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.DictField(child=serializers.CharField())


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
                    | Q(
                        workspace__is_default=True, workspace__organization=organization
                    )
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
    filters = bounded_filter_list_query_param_field(required=False, default=list)
    page_number = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text=BOUNDED_PAGE_NUMBER_HELP_TEXT,
    )
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    allow_sampled = serializers.BooleanField(
        required=False,
        help_text=(
            "Omit for backward-compatible complete bounded pages, which may "
            "label total_rows as a lower bound. Send false to require an exact "
            "total, or true to opt in explicitly to lower-bound totals."
        ),
    )


class SpanObserveListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    user_id = serializers.CharField(required=False, allow_blank=True)
    filters = bounded_filter_list_query_param_field(required=False, default=list)
    page_number = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text=BOUNDED_PAGE_NUMBER_HELP_TEXT,
    )
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    cursor = serializers.CharField(
        required=False, allow_blank=False, max_length=4096, help_text=CURSOR_HELP_TEXT
    )
    cursor_mode = serializers.BooleanField(required=False, default=False)
    allow_sampled = serializers.BooleanField(
        required=False,
        help_text=(
            "Omit for backward-compatible complete bounded pages, which may "
            "label total_rows as a lower bound. Send false to require an exact "
            "total, or true to opt in explicitly to lower-bound totals."
        ),
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return validate_cursor_exclusivity(self, attrs, page_field="page_number")


class SpanListMetadataSerializer(serializers.Serializer):
    """Metadata shared by prototype and Observe span list responses."""

    total_rows = serializers.IntegerField(min_value=0)
    total_rows_exact = serializers.IntegerField(
        required=False, min_value=0, allow_null=True
    )
    total_rows_is_lower_bound = serializers.BooleanField(required=False)
    has_more = serializers.BooleanField(required=False)
    next_cursor = serializers.CharField(required=False, allow_null=True)
    next_cursor_fingerprint = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False, allow_null=True
    )
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=("complete", "degraded"), required=False
    )
    query_error_code = serializers.CharField(required=False, allow_null=True)
    query_elapsed_ms = serializers.FloatField(required=False, min_value=0)
    query_count = serializers.IntegerField(required=False, min_value=0)
    query_rows_returned = serializers.IntegerField(required=False, min_value=0)
    query_result_payload_bytes = serializers.IntegerField(required=False, min_value=0)
    query_applied_filter_version = serializers.ChoiceField(
        choices=("canonical-json-sha256-v1",), required=False
    )
    query_applied_filter_sha256 = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False
    )
    query_applied_filter_count = serializers.IntegerField(required=False, min_value=0)


class SpanListColumnConfigSerializer(serializers.Serializer):
    """Typed wire form of ``tracer.utils.helper.FieldConfig``."""

    id = serializers.CharField()
    name = serializers.CharField()
    is_visible = serializers.BooleanField()
    group_by = serializers.CharField(required=False, allow_null=True)
    output_type = serializers.CharField(required=False, allow_null=True)
    reverse_output = serializers.BooleanField(required=False, allow_null=True)
    annotation_label_type = serializers.CharField(required=False, allow_null=True)
    choices = serializers.ListField(
        child=serializers.CharField(allow_null=True),
        required=False,
        allow_null=True,
    )
    settings = JsonValueField(required=False, allow_null=True)
    choices_map = JsonValueField(required=False, allow_null=True)
    eval_template_id = serializers.CharField(required=False, allow_null=True)
    annotators = JsonValueField(required=False, allow_null=True)
    source_field = serializers.CharField(required=False, allow_null=True)
    parent_eval_id = serializers.CharField(required=False, allow_null=True)
    property_id = serializers.CharField(required=False, allow_null=True)
    property_kind = serializers.CharField(required=False, allow_null=True)
    property_source = serializers.CharField(required=False, allow_null=True)


class SpanPrototypeListResultSerializer(serializers.Serializer):
    column_config = SpanListColumnConfigSerializer(many=True)
    metadata = SpanListMetadataSerializer()
    table = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )


class SpanPrototypeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SpanPrototypeListResultSerializer()


class SpanObserveListResultSerializer(serializers.Serializer):
    metadata = SpanListMetadataSerializer()
    table = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )
    config = SpanListColumnConfigSerializer(many=True)


class SpanObserveListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SpanObserveListResultSerializer()


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
