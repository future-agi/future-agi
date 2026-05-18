from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import (
    ObserveGraphDataRequestSerializer,
    SortParamListQueryParamField,
    StrictInputSerializer,
    filter_list_field,
    filter_list_query_param_field,
)
from tracer.utils.helper import validate_filters_helper, validate_sort_params_helper


class TraceSessionSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )

    class Meta:
        model = TraceSession
        fields = ["id", "project", "bookmarked", "name", "created_at"]


class TraceSessionExportSerializer(serializers.Serializer):
    filters = filter_list_field(required=False, default=[])
    sort_params = serializers.ListField(
        required=False, default=[], child=serializers.JSONField()
    )

    def validate_filters(self, value):
        return validate_filters_helper(value)

    def validate_sort_params(self, value):
        return validate_sort_params_helper(value)


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
