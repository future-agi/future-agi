"""Snapshot creation and read contracts for the dataset-version API."""

from rest_framework import serializers

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.serializer_fields import JsonValueField


class SnapshotTextField(serializers.CharField):
    """Preserve provenance text without accepting numeric string coercion."""

    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class SnapshotInputField(JsonValueField):
    class Meta:
        swagger_schema_fields = {
            "x-json-value": True,
            "x-nullable": False,
            "description": "Any non-null JSON value; nested nulls are allowed.",
        }


class DatasetVersionItemCreateSerializer(StrictInputSerializer):
    input = SnapshotInputField()
    expected = JsonValueField(required=False, allow_null=True)
    metadata = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    example_id = serializers.UUIDField(required=False, allow_null=True)
    external_id = SnapshotTextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=500,
        trim_whitespace=False,
    )
    source_trace_id = SnapshotTextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=255,
        trim_whitespace=False,
    )
    source_span_id = SnapshotTextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=255,
        trim_whitespace=False,
    )
    legacy_row_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DatasetVersionCreateSerializer(StrictInputSerializer):
    items = serializers.ListField(
        child=DatasetVersionItemCreateSerializer(), max_length=10_000
    )
    parent_version_id = serializers.UUIDField(required=False, allow_null=True)
    description = SnapshotTextField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )
    metadata = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    source_revision = SnapshotTextField(
        required=False,
        allow_blank=True,
        max_length=255,
        trim_whitespace=False,
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


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
    description = serializers.CharField(read_only=True, allow_blank=True)
    source_revision = serializers.CharField(read_only=True, allow_blank=True)

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


class DatasetVersionCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetVersionReadSerializer()


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
