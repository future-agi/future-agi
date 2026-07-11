from django.db.models import Q
from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import (
    ObserveGraphDataRequestSerializer,
    SortParamListQueryParamField,
    StrictInputSerializer,
    filter_list_query_param_field,
)


class TraceSessionSerializer(serializers.ModelSerializer):
    """A trace session groups all the traces a single end-user conversation/run produced in an observe project — its traces (and their spans) share one ``session.id``. Browse sessions for a project via list_sessions and open one with get_session to see its session-level cost/token/duration aggregates and the traces it contains."""

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text="UUID of the trace project this session belongs to (from list_projects).",
    )

    class Meta:
        model = TraceSession
        fields = ["id", "project", "bookmarked", "name", "created_at"]
        extra_kwargs = {
            "id": {
                "help_text": "UUID of the session; pass it to get_session as the session id."
            },
            "bookmarked": {
                "help_text": "Whether a user has bookmarked/starred this session."
            },
            "name": {
                "help_text": "The session identifier the SDK sent in the OTel ``session.id`` attribute (the user-facing session id, distinct from the UUID)."
            },
            "created_at": {"help_text": "When this session record was first created."},
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

        scope = Q(organization=organization)
        workspace = getattr(request, "workspace", None)
        if workspace:
            if getattr(workspace, "is_default", False):
                scope &= (
                    Q(workspace=workspace)
                    | Q(
                        workspace__is_default=True,
                        workspace__organization=organization,
                    )
                    | Q(workspace__isnull=True)
                )
            else:
                scope &= Q(workspace=workspace)

        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        self.fields["project"].queryset = project_manager.filter(
            scope,
            deleted=False,
        )


class TraceSessionFilterValuesQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=True)
    column = serializers.ChoiceField(
        choices=["session_id", "user_id", "first_message", "last_message"]
    )
    search = serializers.CharField(required=False, allow_blank=True, default="")
    page = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=50, min_value=1, max_value=500
    )


class TraceSessionListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    user_id = serializers.CharField(required=False, allow_blank=True)
    bookmarked = serializers.BooleanField(required=False, allow_null=True)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
    interval = serializers.CharField(required=False, allow_blank=True)


class TraceSessionExportQuerySerializer(TraceSessionListQuerySerializer):
    project_id = serializers.UUIDField()


class TraceSessionRetrieveQuerySerializer(StrictInputSerializer):
    user_id = serializers.CharField(required=False, allow_blank=True)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class TraceSessionGraphDataRequestSerializer(ObserveGraphDataRequestSerializer):
    pass
