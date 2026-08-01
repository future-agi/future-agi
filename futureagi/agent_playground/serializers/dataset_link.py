from rest_framework import serializers

from model_hub.models.develop_dataset import Column


class ColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Column
        fields = ["id", "name", "data_type"]
        read_only_fields = fields


class CellUpdateSerializer(serializers.Serializer):
    value = serializers.CharField(allow_blank=True, allow_null=True, default="")


class DeleteRowsSerializer(serializers.Serializer):
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=[],
        help_text="List of row UUIDs to delete.",
    )
    select_all = serializers.BooleanField(required=False, default=False)
    exclude_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=[],
        help_text="Row UUIDs to exclude when using select_all.",
    )

    def validate(self, attrs):
        select_all = attrs.get("select_all", False)
        row_ids = attrs.get("row_ids", [])
        exclude_ids = attrs.get("exclude_ids", [])

        if select_all and row_ids:
            raise serializers.ValidationError(
                "Cannot provide both 'select_all' and 'row_ids'."
            )
        if not select_all and not row_ids:
            raise serializers.ValidationError(
                "A list of row IDs or select_all flag is required for deletion."
            )
        if not select_all and exclude_ids:
            raise serializers.ValidationError(
                "'exclude_ids' can only be used with 'select_all'."
            )
        return attrs


class ExecuteRequestSerializer(serializers.Serializer):
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=None,
        help_text="Optional list of row IDs to execute. If omitted, all rows are executed.",
    )
    task_queue = serializers.CharField(
        required=False,
        default="tasks_l",
    )

    def validate_row_ids(self, value):
        if value is not None and not value:
            raise serializers.ValidationError(
                "row_ids must include at least one row ID or be omitted."
            )
        return value
