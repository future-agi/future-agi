from rest_framework import serializers

from tracer.models.project import Project
from tracer.serializers.filters import (
    JsonValueField,
    MetricSortParamListField,
    ObserveGraphMetricConfigField,
    StrictInputSerializer,
    filter_list_field,
    filter_list_query_param_field,
)


class ProjectSerializer(serializers.ModelSerializer):
    config = JsonValueField(required=False, allow_null=True)
    session_config = JsonValueField(required=False, allow_null=True)
    tags = JsonValueField(required=False, allow_null=True)

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


class ProjectDetailResultSerializer(ProjectSerializer):
    sampling_rate = serializers.FloatField()

    class Meta(ProjectSerializer.Meta):
        fields = ProjectSerializer.Meta.fields + ["sampling_rate"]


class ProjectDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ProjectDetailResultSerializer()


class ProjectIdListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    trace_type = serializers.CharField()


class ProjectIdListResultSerializer(serializers.Serializer):
    projects = ProjectIdListItemSerializer(many=True)


class ProjectIdListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ProjectIdListResultSerializer()


class ProjectNameUpdateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=True)
    name = serializers.CharField(required=True)
    sampling_rate = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)


class ProjectVersionExportSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=True)
    runs_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True, default=list
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
    req_data_config = ObserveGraphMetricConfigField(required=False, default=dict)


class ProjectUserGraphDataQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    end_user_id = serializers.UUIDField()


class ProjectUserGraphDataRequestSerializer(StrictInputSerializer):
    interval = serializers.CharField(required=False, default="hour", allow_blank=False)
    filters = filter_list_field(required=False, default=list)
