from rest_framework import serializers

from tracer.models.project import Project
from tracer.serializers.filters import (
    MetricSortParamListField,
    StrictInputSerializer,
    filter_list_field,
    filter_list_query_param_field,
)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            "id",
            "model_type",
            "name",
            "trace_type",
            "metadata",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
            "config",
            "source",
            "session_config",
            "tags",
        ]
        read_only_fields = ["organization", "workspace"]


class ProjectNameUpdateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=True)
    name = serializers.CharField(required=True)
    sampling_rate = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)


class ProjectVersionExportSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=True)
    runs_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True
    )
    filters = filter_list_field(required=False, default=list)
    sort_params = MetricSortParamListField(required=False, default=list)


class ProjectGraphDataQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    interval = serializers.CharField(required=False, default="hour", allow_blank=False)
    filters = filter_list_query_param_field(required=False, default=list)


class ProjectUserMetricsRequestSerializer(StrictInputSerializer):
    end_user_id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    interval = serializers.CharField(required=False, default="day", allow_blank=False)
    filters = filter_list_field(required=False, default=list)


class ProjectUsersAggregateGraphDataRequestSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    interval = serializers.CharField(required=False, default="day", allow_blank=False)
    filters = filter_list_field(required=False, default=list)
    property = serializers.CharField(
        required=False, default="average", allow_blank=False
    )
    req_data_config = serializers.DictField(
        child=serializers.JSONField(), required=False, default=dict
    )


class ProjectUserGraphDataQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    end_user_id = serializers.UUIDField()


class ProjectUserGraphDataRequestSerializer(serializers.Serializer):
    interval = serializers.CharField(required=False, default="hour", allow_blank=False)
    filters = filter_list_field(required=False, default=list)
