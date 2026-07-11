from django.db.models import Q
from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import (
    SortParamListQueryParamField,
    StrictInputSerializer,
    filter_list_query_param_field,
)


class TraceSerializer(serializers.ModelSerializer):
    """A trace: one end-to-end request/run recorded in a trace project, grouping the
    spans (LLM calls, tools, retrievals) emitted for it. Listed/read via
    list_traces / get_trace and created/edited via create_trace / update_trace.
    `input`/`output` hold the trace's request and response payloads, `error` the
    failure detail if any, `metadata`/`tags` arbitrary annotations, and `session`
    links it to a multi-turn conversation."""

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text="UUID of the trace project this trace belongs to (from list_projects).",
    )
    project_version = serializers.PrimaryKeyRelatedField(
        queryset=ProjectVersion.objects.all(),
        many=False,
        required=False,
        allow_null=True,
        help_text=(
            "Optional UUID of the project version this trace was recorded "
            "against (from list_project_versions); must belong to the same project."
        ),
    )
    session = serializers.PrimaryKeyRelatedField(
        queryset=TraceSession.objects.all(),
        many=False,
        required=False,
        allow_null=True,
        help_text=(
            "Optional UUID of the trace session that groups this trace into a "
            "multi-turn conversation (from list_sessions); must belong to the same project."
        ),
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
        extra_kwargs = {
            "name": {"help_text": "Human-readable name of the trace (e.g. the operation or endpoint)."},
            "metadata": {"help_text": "Arbitrary JSON metadata attached to the trace."},
            "input": {"help_text": "JSON input/request payload that started the trace."},
            "output": {"help_text": "JSON output/response payload the trace produced."},
            "error": {"help_text": "JSON error detail if the trace failed; null otherwise."},
            "external_id": {"help_text": "Optional external/caller-supplied identifier for the trace."},
            "tags": {"help_text": "List of string tags used to label and filter the trace."},
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


class TraceListQuerySerializer(StrictInputSerializer):
    project_version_id = serializers.UUIDField(required=True)
    trace_ids = CommaSeparatedStringListField(required=False, default=list)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class TraceObserveListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    project_version_id = serializers.UUIDField(required=False)
    session_id = serializers.UUIDField(required=False)
    filters = filter_list_query_param_field(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    interval = serializers.CharField(required=False, allow_blank=True)


class TraceObserveListMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()


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


class TraceExportQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class TraceVoiceCallListQuerySerializer(TraceExportQuerySerializer):
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    remove_simulation_calls = serializers.BooleanField(required=False, default=False)


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


class UsersQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=500)
    current_page_index = serializers.IntegerField(required=False, min_value=0)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    filters = filter_list_query_param_field(required=False, default=list)
    export = serializers.BooleanField(required=False, default=False)


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


class TraceDetailResultSerializer(serializers.Serializer):
    """Envelope payload for the trace-detail endpoint (CH-assembled)."""

    trace = serializers.JSONField()
    observation_spans = serializers.ListField(child=serializers.JSONField())
    summary = serializers.JSONField()
    graph = serializers.JSONField()


class TraceDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceDetailResultSerializer()
