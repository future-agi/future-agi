from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.serializers.filters import (
    MetricSortParamListQueryParamField,
    StrictInputSerializer,
    filter_list_query_param_field,
)


class ProjectVersionSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )

    class Meta:
        model = ProjectVersion
        fields = [
            "id",
            "project",
            "name",
            "metadata",
            "start_time",
            "end_time",
            "error",
            "eval_tags",
            "avg_eval_score",
            "version",
            "annotations",
        ]
        read_only_fields = ["version"]


class ProjectVersionRunsQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = MetricSortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
