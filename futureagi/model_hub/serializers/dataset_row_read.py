"""Wire contract for reading one dataset row."""

from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField

ROW_SOURCE_TYPES = (
    "trace",
    "span",
    "session",
    "file",
    "huggingface",
    "generated",
    "copied",
)


class DatasetRowReadCellMetadataSerializer(serializers.Serializer):
    response_time_ms = serializers.FloatField(allow_null=True)
    token_count = serializers.IntegerField(allow_null=True)
    annotation = serializers.ChoiceField(
        choices=["auto", "verified", "human"], allow_null=True
    )


class DatasetRowReadCellSerializer(serializers.Serializer):
    """One column's value in the row, as get-row-data returns it."""

    cell_id = serializers.UUIDField()
    cell_value = serializers.CharField(allow_null=True, allow_blank=True)
    metadata = DatasetRowReadCellMetadataSerializer()
    status = serializers.CharField(allow_blank=True)
    value_infos = JsonValueField(allow_null=True)
    feedback_info = JsonValueField(allow_null=True)


class DatasetRowReadSourceSerializer(serializers.Serializer):
    """Where the row came from. Ids are present only when the source has them."""

    type = serializers.ChoiceField(choices=ROW_SOURCE_TYPES)
    label = serializers.CharField()
    project_id = serializers.UUIDField(required=False)
    trace_id = serializers.CharField(required=False)
    span_id = serializers.CharField(required=False)
    session_id = serializers.CharField(required=False)
    file_name = serializers.CharField(required=False)
    expired = serializers.BooleanField(required=False)


class DatasetRowReadMetaSerializer(serializers.Serializer):
    source = DatasetRowReadSourceSerializer(required=False)


class DatasetRowReadResultSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    order = serializers.IntegerField()
    row_meta = DatasetRowReadMetaSerializer()
    # One entry per visible column, keyed by the column id.
    cells = serializers.DictField(child=DatasetRowReadCellSerializer())


class DatasetRowReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRowReadResultSerializer()
