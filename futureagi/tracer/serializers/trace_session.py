from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import filter_list_field
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
