from django.db.models import Q
from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.serializers.cursor_pagination import (
    CURSOR_HELP_TEXT,
    validate_cursor_exclusivity,
)
from tracer.serializers.filters import (
    BOUNDED_PAGE_NUMBER_HELP_TEXT,
    ObserveGraphDataRequestSerializer,
    SortParamListQueryParamField,
    StrictInputSerializer,
    session_bounded_filter_list_field,
    session_bounded_filter_list_query_param_field,
    session_filter_list_query_param_field,
)


class TraceSessionSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )

    class Meta:
        model = TraceSession
        fields = ["id", "project", "bookmarked", "name", "created_at"]

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
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=512,
    )
    page = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=50, min_value=1, max_value=500
    )

    def validate_search(self, value):
        from tracer.services.clickhouse.attribute_reads import (
            InvalidAttributeSearch,
            validate_attribute_search,
        )

        try:
            return validate_attribute_search(value)
        except InvalidAttributeSearch as exc:
            raise serializers.ValidationError(str(exc)) from exc


class TraceSessionListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    user_id = serializers.CharField(required=False, allow_blank=True)
    bookmarked = serializers.BooleanField(required=False, allow_null=True)
    filters = session_bounded_filter_list_query_param_field(
        required=False, default=list
    )
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
    cursor = serializers.CharField(
        required=False, allow_blank=False, max_length=4096, help_text=CURSOR_HELP_TEXT
    )
    cursor_mode = serializers.BooleanField(required=False, default=False)
    interval = serializers.CharField(required=False, allow_blank=True)
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


class TraceSessionExportQuerySerializer(TraceSessionListQuerySerializer):
    project_id = serializers.UUIDField()


class TraceSessionRetrieveQuerySerializer(StrictInputSerializer):
    user_id = serializers.CharField(required=False, allow_blank=True)
    filters = session_filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class TraceSessionGraphDataRequestSerializer(ObserveGraphDataRequestSerializer):
    filters = session_bounded_filter_list_field(required=False, default=list)
