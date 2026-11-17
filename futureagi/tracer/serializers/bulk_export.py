from rest_framework import serializers

from tracer.models.bulk_export import BulkExportJob
from tracer.serializers.filters import StrictInputSerializer
from tracer.serializers.observation_span import SpanObserveListQuerySerializer
from tracer.serializers.trace import TraceObserveListQuerySerializer
from tracer.serializers.trace_session import TraceSessionListQuerySerializer


QUERY_SERIALIZERS = {
    "traces": TraceObserveListQuerySerializer,
    "spans": SpanObserveListQuerySerializer,
    "sessions": TraceSessionListQuerySerializer,
}


def validate_export_query(row_type, options, project_id):
    # Pagination, scope and approximation are always owned by the worker.
    if not isinstance(options, dict) or set(options) & {
        "project_id", "page_number", "page_size", "cursor", "cursor_mode", "allow_sampled",
    }:
        raise serializers.ValidationError("Invalid export filter options.")
    serializer = QUERY_SERIALIZERS[row_type](data={**options, "project_id": str(project_id)})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class BulkExportCreateSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    row_type = serializers.ChoiceField(choices=BulkExportJob.ROW_TYPES)
    filters = serializers.JSONField(required=False, default=dict)
    format = serializers.ChoiceField(choices=BulkExportJob.FORMATS, default="csv")

    def validate(self, attrs):
        validate_export_query(attrs["row_type"], attrs["filters"], attrs["project_id"])
        return attrs


class BulkExportStatusSerializer(serializers.ModelSerializer):
    download_url = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = BulkExportJob
        fields = [
            "id", "project_id", "row_type", "format", "status", "row_count",
            "error", "created_at", "updated_at", "started_at", "finished_at", "download_url",
        ]
        read_only_fields = fields
