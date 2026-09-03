import json

from django.db.models import Q
from rest_framework import serializers

from tfc.utils.serializer_fields import JSON_VALUE_SCHEMA, JsonValueField
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.serializers.cursor_pagination import (
    CURSOR_HELP_TEXT,
    validate_cursor_exclusivity,
)
from tracer.serializers.filters import (
    BOUNDED_PAGE_NUMBER_HELP_TEXT,
    JsonObjectField,
    SortParamListQueryParamField,
    StrictInputSerializer,
    bounded_filter_list_query_param_field,
    filter_list_query_param_field,
)
from tracer.services.user_attribute_contract import unsupported_user_attribute_keys


class TraceSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )
    project_version = serializers.PrimaryKeyRelatedField(
        queryset=ProjectVersion.objects.all(),
        many=False,
        required=False,
        allow_null=True,
    )
    session = serializers.PrimaryKeyRelatedField(
        queryset=TraceSession.objects.all(),
        many=False,
        required=False,
        allow_null=True,
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

        project_scope = Q(organization=organization)
        related_project_scope = Q(project__organization=organization)
        workspace = getattr(request, "workspace", None)
        if workspace:
            if getattr(workspace, "is_default", False):
                project_scope &= (
                    Q(workspace=workspace)
                    | Q(
                        workspace__is_default=True, workspace__organization=organization
                    )
                    | Q(workspace__isnull=True)
                )
                related_project_scope &= (
                    Q(project__workspace=workspace)
                    | Q(
                        project__workspace__is_default=True,
                        project__workspace__organization=organization,
                    )
                    | Q(project__workspace__isnull=True)
                )
            else:
                project_scope &= Q(workspace=workspace)
                related_project_scope &= Q(project__workspace=workspace)

        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        self.fields["project"].queryset = project_manager.filter(
            project_scope, deleted=False
        )

        project_version_manager = getattr(
            ProjectVersion, "no_workspace_objects", ProjectVersion.objects
        )
        self.fields["project_version"].queryset = project_version_manager.filter(
            related_project_scope,
            project__deleted=False,
            deleted=False,
        )

        trace_session_manager = getattr(
            TraceSession, "no_workspace_objects", TraceSession.objects
        )
        self.fields["session"].queryset = trace_session_manager.filter(
            related_project_scope,
            project__deleted=False,
            deleted=False,
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        project = attrs.get("project") or getattr(instance, "project", None)
        project_version = attrs.get("project_version")
        if "project_version" not in attrs and instance is not None:
            project_version = instance.project_version
        session = attrs.get("session")
        if "session" not in attrs and instance is not None:
            session = instance.session

        if project_version and project and project_version.project_id != project.id:
            raise serializers.ValidationError(
                {
                    "project_version": "Project version must belong to the selected project."
                }
            )

        if session and project and session.project_id != project.id:
            raise serializers.ValidationError(
                {"session": "Session must belong to the selected project."}
            )

        return attrs


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


class JSONOrCommaSeparatedStringListField(CommaSeparatedStringListField):
    """Accept an exact JSON string list, retaining CSV compatibility.

    Attribute paths are user data and may themselves contain commas. New
    callers send a JSON array so those keys round-trip exactly; the historical
    comma-separated shape remains accepted for simple keys.
    """

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, str) and data.lstrip().startswith("["):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    "Value must be a JSON string array."
                ) from exc
            if not isinstance(data, list):
                raise serializers.ValidationError("Value must be a JSON string array.")
            if not all(isinstance(item, str) for item in data):
                raise serializers.ValidationError(
                    "Every attribute key must be a string."
                )
        return super().to_internal_value(data)


class TraceListQuerySerializer(StrictInputSerializer):
    project_version_id = serializers.UUIDField(required=True)
    trace_ids = CommaSeparatedStringListField(required=False, default=list)
    filters = bounded_filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
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


class TraceObserveListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    project_version_id = serializers.UUIDField(required=False)
    session_id = serializers.UUIDField(required=False)
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
    attribute_keys = JSONOrCommaSeparatedStringListField(
        required=False,
        help_text=(
            "JSON-encoded list of custom attribute keys to hydrate; only "
            "requested keys are returned. Each key resolves to its latest live "
            "span value by (start_time, span_id). Comma-separated simple keys "
            "remain supported."
        ),
    )
    allow_sampled = serializers.BooleanField(
        required=False,
        help_text=(
            "Omit for backward-compatible complete bounded pages, which may "
            "label total_rows as a lower bound. Send false to require an exact "
            "total. Send true to opt in explicitly to lower-bound totals and, "
            "on the first page, a clearly labelled bounded partial result when "
            "the full ordered prefix cannot be proven inside the read budget."
        ),
    )
    interval = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        attribute_keys = tuple(dict.fromkeys(attrs.get("attribute_keys") or ()))
        if len(attribute_keys) > 100 or any(len(key) > 512 for key in attribute_keys):
            raise serializers.ValidationError(
                {
                    "attribute_keys": "Request at most 100 attribute keys (512 chars each)."
                }
            )
        if sum(len(key.encode("utf-8")) for key in attribute_keys) > 2_048:
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Combined attribute keys must be at most 2048 UTF-8 bytes."
                    )
                }
            )
        attrs["attribute_keys"] = list(attribute_keys)
        return validate_cursor_exclusivity(self, attrs, page_field="page_number")


class TraceObserveListMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()
    total_rows_exact = serializers.IntegerField(required=False, allow_null=True)
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
    query_elapsed_ms = serializers.FloatField(required=False)
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


class TraceSessionListMetadataSerializer(TraceObserveListMetadataSerializer):
    """Session-list page completeness plus non-exact candidate ordering."""

    query_exact = serializers.BooleanField(required=False)
    query_provenance = serializers.ChoiceField(
        choices=("spans_per_session_candidate",), required=False
    )
    ordering_exact = serializers.BooleanField(required=False)


class TraceObserveColumnConfigSerializer(serializers.Serializer):
    """One column-config row — the asdict() shape of tracer.utils.helper.FieldConfig."""

    id = serializers.CharField()
    name = serializers.CharField()
    is_visible = serializers.BooleanField()
    group_by = serializers.CharField(required=False, allow_null=True)
    output_type = serializers.CharField(required=False, allow_null=True)
    reverse_output = serializers.BooleanField(required=False, allow_null=True)
    annotation_label_type = serializers.CharField(required=False, allow_null=True)
    # FieldConfig defaults `choices` to (None,), so serialized rows can carry
    # [None] — the child must allow null.
    choices = serializers.ListField(
        child=serializers.CharField(allow_null=True), required=False, allow_null=True
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


class TraceObserveListResultSerializer(serializers.Serializer):
    metadata = TraceObserveListMetadataSerializer()
    # allow_null: real rows carry null cells (cost, latency on error traces).
    table = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )
    config = TraceObserveColumnConfigSerializer(many=True)


class TraceObserveListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TraceObserveListResultSerializer()


class TracePropertiesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.ListField(child=serializers.CharField())


class _ExtraSessionCellsMixin:
    """Validate and preserve tenant-defined session table cells."""

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        dynamic_cell = JsonValueField(allow_null=True)
        for key, value in data.items():
            if key not in self.fields:
                validated[key] = dynamic_cell.run_validation(value)
        return validated

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if isinstance(instance, dict):
            dynamic_cell = JsonValueField(allow_null=True)
            for key, value in instance.items():
                if key not in self.fields:
                    representation[key] = dynamic_cell.to_representation(value)
        return representation


class TraceSessionTableRowSerializer(_ExtraSessionCellsMixin, serializers.Serializer):
    """Typed core cells for one Observe session row.

    Session rows may also carry dynamic attribute and annotation-label columns.
    Stable API-owned cells remain typed, while dynamic attribute/annotation
    cells are validated as JSON and passed through unchanged.
    """

    # Older/synthetic session projections may expose only dynamic cells. Keep
    # the canonical identity typed when present without narrowing that existing
    # response contract.
    session_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    session_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    project_id = serializers.UUIDField(required=False, allow_null=True)
    start_time = serializers.DateTimeField(required=False, allow_null=True)
    end_time = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False, allow_null=True)
    duration = serializers.FloatField(required=False, allow_null=True)
    total_cost = serializers.FloatField(required=False, allow_null=True)
    total_tokens = serializers.IntegerField(required=False, allow_null=True)
    total_traces_count = serializers.IntegerField(required=False, allow_null=True)
    first_message = JsonValueField(required=False, allow_null=True)
    last_message = JsonValueField(required=False, allow_null=True)
    user_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    user_id_type = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    user_id_hash = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )

    class Meta:
        swagger_schema_fields = {
            "additionalProperties": {
                **JSON_VALUE_SCHEMA,
                "x-nullable": True,
            }
        }


class TraceSessionListResultSerializer(TraceObserveListResultSerializer):
    metadata = TraceSessionListMetadataSerializer()
    table = TraceSessionTableRowSerializer(many=True)


class TraceSessionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TraceSessionListResultSerializer()


class TracePrototypeListResultSerializer(serializers.Serializer):
    """Prototype trace list wire shape (uses ``column_config``)."""

    column_config = TraceObserveColumnConfigSerializer(many=True)
    metadata = TraceObserveListMetadataSerializer()
    table = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )


class TracePrototypeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TracePrototypeListResultSerializer()


class TraceExportQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    attribute_keys = JSONOrCommaSeparatedStringListField(
        required=False,
        help_text=(
            "JSON-encoded list of custom attribute keys to include as CSV "
            "columns. Comma-separated simple keys remain supported."
        ),
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        attribute_keys = tuple(dict.fromkeys(attrs.get("attribute_keys") or ()))
        if len(attribute_keys) > 100 or any(len(key) > 512 for key in attribute_keys):
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Request at most 100 attribute keys (512 chars each)."
                    )
                }
            )
        if sum(len(key.encode("utf-8")) for key in attribute_keys) > 2_048:
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Combined attribute keys must be at most 2048 UTF-8 bytes."
                    )
                }
            )
        attrs["attribute_keys"] = list(attribute_keys)
        return attrs


class TraceVoiceCallListQuerySerializer(TraceExportQuerySerializer):
    page = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        help_text=(
            "One-based numbered page. Pages whose required ordered work exceeds "
            "the finite read contract return HTTP 422 with code "
            "page_depth_exceeded; request an earlier page, use the additive "
            "continuation cursor, or narrow the time range."
        ),
    )
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    remove_simulation_calls = serializers.BooleanField(required=False, default=False)
    cursor = serializers.CharField(
        required=False, allow_blank=False, max_length=4096, help_text=CURSOR_HELP_TEXT
    )
    cursor_mode = serializers.BooleanField(required=False, default=False)
    allow_sampled = serializers.BooleanField(
        required=False,
        help_text=(
            "Omit for backward-compatible complete bounded pages, which may "
            "label count as a lower bound. Send false to require an exact "
            "total. Send true to opt in explicitly to lower-bound totals and, "
            "on the first page, a clearly labelled bounded partial result when "
            "the full ordered prefix cannot be proven inside the read budget."
        ),
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return validate_cursor_exclusivity(
            self,
            attrs,
            page_field="page",
            first_page=1,
        )


class TraceVoiceCallListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    count_is_lower_bound = serializers.BooleanField()
    total_pages = serializers.IntegerField(min_value=0)
    current_page = serializers.IntegerField(min_value=1)
    next = serializers.IntegerField(min_value=1, allow_null=True)
    previous = serializers.IntegerField(min_value=1, allow_null=True)
    results = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )
    config = TraceObserveColumnConfigSerializer(many=True)
    has_more = serializers.BooleanField()
    next_cursor = serializers.CharField(required=False, allow_null=True)
    next_cursor_fingerprint = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False, allow_null=True
    )
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(choices=("complete", "degraded"))
    query_error_code = serializers.CharField(required=False)
    query_applied_filter_version = serializers.ChoiceField(
        choices=("canonical-json-sha256-v1",), required=False
    )
    query_applied_filter_sha256 = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False
    )
    query_applied_filter_count = serializers.IntegerField(required=False, min_value=0)


class TraceVoiceCallDetailQuerySerializer(StrictInputSerializer):
    """Strict compatibility contract for the voice-call detail identity."""

    trace_id = serializers.UUIDField(
        required=False,
        help_text="Voice-call trace UUID. Supply this or the legacy traceId alias.",
    )
    traceId = serializers.UUIDField(  # noqa: N815 - public compatibility alias
        required=False,
        help_text="Legacy alias for trace_id; when both are supplied they must match.",
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        trace_id = attrs.get("trace_id")
        legacy_trace_id = attrs.get("traceId")
        if trace_id is None and legacy_trace_id is None:
            raise serializers.ValidationError(
                {"trace_id": "Supply trace_id or the legacy traceId alias."}
            )
        if (
            trace_id is not None
            and legacy_trace_id is not None
            and trace_id != legacy_trace_id
        ):
            raise serializers.ValidationError(
                {"traceId": "traceId must match trace_id when both are supplied."}
            )
        attrs["trace_id"] = trace_id or legacy_trace_id
        attrs.pop("traceId", None)
        return attrs


class TraceVoiceCallDetailResultSerializer(serializers.Serializer):
    """Stable voice-call detail shape shared by every provider adapter.

    Provider payloads are normalized by ``ObservabilityService`` before this
    response is built.  Keep the normalized fields explicit here: documenting
    the whole result as an arbitrary JSON object made generated clients unable
    to distinguish a valid detail response from any other object.
    """

    id = serializers.CharField()
    trace_id = serializers.CharField()
    project_id = serializers.CharField()
    provider_call_id = serializers.CharField(allow_null=True)

    phone_number = serializers.CharField(required=False, allow_null=True)
    customer_name = serializers.CharField(required=False, allow_null=True)
    call_id = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_null=True)
    started_at = serializers.CharField(required=False, allow_null=True)
    ended_at = serializers.CharField(required=False, allow_null=True)
    created_at = serializers.CharField(required=False, allow_null=True)
    duration_seconds = serializers.IntegerField(required=False, allow_null=True)
    # Provider adapters preserve an explicit empty string when the provider
    # supplied the field but no recording/summary was available.  That is a
    # valid normalized voice-detail value, not response-contract drift.
    recording_url = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    stereo_recording_url = serializers.CharField(required=False, allow_null=True)
    cost_cents = serializers.FloatField(required=False, allow_null=True)
    cost_breakdown = JsonObjectField(required=False, allow_null=True)
    error_message = serializers.CharField(required=False, allow_null=True)
    call_summary = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    ended_reason = serializers.CharField(required=False, allow_null=True)
    overall_score = serializers.FloatField(required=False, allow_null=True)
    response_time_ms = serializers.FloatField(required=False, allow_null=True)
    response_time_seconds = serializers.FloatField(required=False, allow_null=True)
    assistant_id = serializers.CharField(required=False, allow_null=True)
    assistant_phone_number = serializers.CharField(required=False, allow_null=True)
    call_type = serializers.CharField(required=False, allow_null=True)
    message_count = serializers.IntegerField(required=False, allow_null=True)
    transcript_available = serializers.BooleanField(required=False, allow_null=True)

    transcript = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True)),
        required=False,
        allow_null=True,
    )
    messages = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True)),
        required=False,
        allow_null=True,
    )
    analysis_data = JsonObjectField(required=False, allow_null=True)
    evaluation_data = JsonObjectField(required=False, allow_null=True)

    recording = JsonObjectField()
    recording_available = serializers.BooleanField()
    call_metadata = JsonObjectField()
    observation_span = serializers.ListField(
        child=serializers.DictField(child=JsonValueField(allow_null=True))
    )
    eval_outputs = JsonObjectField()

    call_execution_id = serializers.CharField(required=False, allow_null=True)
    test_execution_id = serializers.CharField(required=False, allow_null=True)
    scenario_id = serializers.CharField(required=False, allow_null=True)
    scenario_name = serializers.CharField(required=False, allow_null=True)
    scenario_graph_id = serializers.CharField(required=False, allow_null=True)
    scenario_graph = JsonObjectField(required=False)

    turn_count = serializers.IntegerField(allow_null=True)
    talk_ratio = serializers.FloatField(allow_null=True)
    agent_talk_percentage = serializers.FloatField(allow_null=True)
    bot_talk_pct = serializers.IntegerField(allow_null=True)
    user_talk_pct = serializers.IntegerField(allow_null=True)
    avg_agent_latency_ms = serializers.IntegerField(allow_null=True)
    user_wpm = serializers.IntegerField(allow_null=True)
    bot_wpm = serializers.IntegerField(allow_null=True)
    user_interruption_count = serializers.IntegerField(allow_null=True)
    ai_interruption_count = serializers.IntegerField(allow_null=True)


class TraceVoiceCallDetailResponseSerializer(serializers.Serializer):
    """GeneralMethods envelope for one normalized voice-call detail."""

    status = serializers.BooleanField()
    result = TraceVoiceCallDetailResultSerializer()


class TraceIndexQuerySerializer(StrictInputSerializer):
    trace_id = serializers.UUIDField()
    project_version_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class TraceObserveIndexQuerySerializer(StrictInputSerializer):
    trace_id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class TraceAgentGraphQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    refresh = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Recompute and atomically replace the last exact graph snapshot.",
    )


class TraceAgentGraphNodeSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    span_count = serializers.IntegerField(min_value=0)
    avg_latency_ms = serializers.FloatField(min_value=0)
    total_tokens = serializers.IntegerField(min_value=0)
    total_cost = serializers.FloatField(min_value=0)
    error_count = serializers.IntegerField(min_value=0)
    trace_count = serializers.IntegerField(min_value=0, allow_null=True)
    trace_count_exact = serializers.BooleanField(required=False)
    is_aggregate = serializers.BooleanField(required=False)
    member_count = serializers.IntegerField(required=False, min_value=0)


class TraceAgentGraphEdgeSerializer(serializers.Serializer):
    source = serializers.CharField()
    target = serializers.CharField()
    transition_count = serializers.IntegerField(min_value=0)
    avg_latency_ms = serializers.FloatField(min_value=0)
    total_tokens = serializers.IntegerField(min_value=0)
    total_cost = serializers.FloatField(min_value=0)
    error_count = serializers.IntegerField(min_value=0)
    trace_count = serializers.IntegerField(min_value=0, allow_null=True)
    trace_count_exact = serializers.BooleanField(required=False)
    is_self_loop = serializers.BooleanField()
    is_aggregate = serializers.BooleanField(required=False)


class TraceAgentGraphResultSerializer(serializers.Serializer):
    nodes = TraceAgentGraphNodeSerializer(many=True)
    edges = TraceAgentGraphEdgeSerializer(many=True)
    path_edges = TraceAgentGraphEdgeSerializer(many=True)
    graph_collapsed = serializers.BooleanField(required=False)
    graph_node_limit = serializers.IntegerField(required=False, min_value=1)
    omitted_node_count = serializers.IntegerField(required=False, min_value=0)
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=("complete", "pending"), required=False
    )
    query_sampled = serializers.BooleanField(required=False)
    query_count = serializers.IntegerField(required=False, min_value=0)
    query_rows_returned = serializers.IntegerField(required=False, min_value=0)
    query_elapsed_ms = serializers.FloatField(required=False, min_value=0)
    query_completed_at = serializers.DateTimeField(required=False)
    query_cached = serializers.BooleanField(required=False)
    query_refresh_failed = serializers.BooleanField(required=False)
    query_refreshing = serializers.BooleanField(required=False)


class TraceAgentGraphResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TraceAgentGraphResultSerializer()


class UsersQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=500)
    current_page_index = serializers.IntegerField(required=False, min_value=0)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    filters = filter_list_query_param_field(required=False, default=list)
    export = serializers.BooleanField(required=False, default=False)
    cursor = serializers.CharField(
        required=False, allow_blank=False, max_length=4096, help_text=CURSOR_HELP_TEXT
    )
    cursor_mode = serializers.BooleanField(required=False, default=False)
    requested_columns = JSONOrCommaSeparatedStringListField(
        required=False,
        default=list,
        help_text=(
            "JSON-encoded list of visible Users-table fields. Raw-derived "
            "metrics are hydrated only when explicitly requested."
        ),
    )
    attribute_keys = JSONOrCommaSeparatedStringListField(
        required=False,
        default=list,
        help_text=(
            "JSON-encoded list of visible custom user attribute keys. Only "
            "these keys (plus keys required by filters) are hydrated."
        ),
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        requested_columns = tuple(dict.fromkeys(attrs.get("requested_columns") or ()))
        attribute_keys = tuple(dict.fromkeys(attrs.get("attribute_keys") or ()))
        if len(requested_columns) > 100 or any(
            len(column) > 128 for column in requested_columns
        ):
            raise serializers.ValidationError(
                {
                    "requested_columns": (
                        "Request at most 100 Users columns (128 chars each)."
                    )
                }
            )
        if len(attribute_keys) > 100 or any(len(key) > 512 for key in attribute_keys):
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Request at most 100 attribute keys (512 chars each)."
                    )
                }
            )
        if sum(len(key.encode("utf-8")) for key in attribute_keys) > 2_048:
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Combined attribute keys must be at most 2048 UTF-8 bytes."
                    )
                }
            )
        unsupported_projection_keys = unsupported_user_attribute_keys(attribute_keys)
        if unsupported_projection_keys:
            raise serializers.ValidationError(
                {
                    "attribute_keys": (
                        "Observe Users does not support payload attribute keys: "
                        + ", ".join(unsupported_projection_keys)
                    )
                }
            )
        unsupported_filter_keys = unsupported_user_attribute_keys(
            item.get("column_id") or item.get("columnId")
            for item in attrs.get("filters", [])
        )
        if unsupported_filter_keys:
            raise serializers.ValidationError(
                {
                    "filters": (
                        "Observe Users does not support payload attribute keys: "
                        + ", ".join(unsupported_filter_keys)
                    )
                }
            )
        attrs["requested_columns"] = list(requested_columns)
        attrs["attribute_keys"] = list(attribute_keys)
        return validate_cursor_exclusivity(
            self,
            attrs,
            page_field="current_page_index",
            first_page=0,
        )


class UsersTableRowSerializer(serializers.Serializer):
    user_id = serializers.CharField(required=False, allow_null=True)
    total_cost = serializers.FloatField()
    total_tokens = serializers.IntegerField(required=False, allow_null=True)
    input_tokens = serializers.IntegerField(required=False, allow_null=True)
    output_tokens = serializers.IntegerField(required=False, allow_null=True)
    num_traces = serializers.IntegerField(required=False, allow_null=True)
    num_sessions = serializers.IntegerField(required=False, allow_null=True)
    num_sessions_is_approximate = serializers.BooleanField(required=False)
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
    table = UsersTableRowSerializer(many=True)
    total_count = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    count_is_lower_bound = serializers.BooleanField(required=False)
    has_more = serializers.BooleanField(required=False)
    next_cursor = serializers.CharField(required=False, allow_null=True)
    next_cursor_fingerprint = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False, allow_null=True
    )
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=("complete", "degraded"), required=False
    )
    query_exact = serializers.BooleanField(required=False)
    query_provenance = serializers.ChoiceField(
        choices=("span_user_rollup_end_users_candidate",), required=False
    )
    ordering_exact = serializers.BooleanField(required=False)
    approximate_fields = serializers.ListField(
        child=serializers.ChoiceField(choices=("num_sessions",)),
        required=False,
    )


class UsersResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = UsersResultSerializer()


class UserCodeExampleResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()


class TraceDetailResultSerializer(serializers.Serializer):
    """Envelope payload for the trace-detail endpoint (CH-assembled)."""

    trace = serializers.JSONField()
    observation_spans = serializers.ListField(child=serializers.JSONField())
    summary = serializers.JSONField()
    graph = serializers.JSONField()


class TraceDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceDetailResultSerializer()
