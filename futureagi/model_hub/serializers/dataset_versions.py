"""Read serializers for the dataset-version compatibility API."""

from rest_framework import serializers

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem


class DatasetVersionPageQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(default=100, min_value=1, max_value=500)
    offset = serializers.IntegerField(default=0, min_value=0, max_value=2147483647)


class DatasetVersionItemReadSerializer(serializers.ModelSerializer):
    """Stable example identity and source provenance for one version item."""

    class Meta:
        model = DatasetVersionItem
        fields = [
            "id",
            "example_id",
            "external_id",
            "input",
            "expected",
            "metadata",
            "source_trace_id",
            "source_span_id",
            "content_hash",
            "legacy_row",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DatasetVersionReadSerializer(serializers.ModelSerializer):
    """Version metadata returned by list and detail endpoints."""

    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DatasetVersion
        fields = [
            "id",
            "dataset",
            "parent_version",
            "created_by",
            "organization",
            "workspace",
            "content_hash",
            "description",
            "metadata",
            "source_revision",
            "created_at",
            "updated_at",
            "item_count",
        ]
        read_only_fields = fields


class DatasetVersionListResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    versions = DatasetVersionReadSerializer(many=True)
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class DatasetVersionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetVersionListResultSerializer()


class DatasetVersionDetailResultSerializer(serializers.Serializer):
    version = DatasetVersionReadSerializer()
    items = DatasetVersionItemReadSerializer(many=True)
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class DatasetVersionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetVersionDetailResultSerializer()
