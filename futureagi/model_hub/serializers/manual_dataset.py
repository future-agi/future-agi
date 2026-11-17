"""Validated schema for creating a blank dataset."""

from rest_framework import serializers

from model_hub.constants import MAX_MANUAL_COLUMNS, MAX_MANUAL_ROWS
from tracer.serializers.filters import StrictInputSerializer

MANUAL_COLUMN_TYPES = (
    "text",
    "integer",
    "float",
    "boolean",
    "json",
    "array",
    "datetime",
)
MANUAL_COLUMN_ROLES = ("input", "expected", "metadata")
ZERO_ROWS_NEED_COLUMNS = (
    'To create an empty dataset with no rows, send "number_of_rows": 0 with a '
    '"columns" list that names each column, for example '
    '[{"name": "input", "type": "text"}].'
)


class ManualDatasetColumnSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=2000)
    type = serializers.ChoiceField(choices=MANUAL_COLUMN_TYPES, default="text")
    role = serializers.ChoiceField(choices=MANUAL_COLUMN_ROLES, default="metadata")


class ManualDatasetSchemaRequestSerializer(serializers.Serializer):
    dataset_name = serializers.CharField(max_length=2000)
    number_of_rows = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_MANUAL_ROWS, default=1
    )
    number_of_columns = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_MANUAL_COLUMNS
    )
    columns = ManualDatasetColumnSerializer(
        many=True, required=False, allow_empty=True, max_length=MAX_MANUAL_COLUMNS
    )

    def validate(self, attrs):
        columns = attrs.get("columns")
        if not columns:
            # Normalize an empty list to the legacy count-only creation path.
            attrs.pop("columns", None)
            # Zero rows is only for a named schema; a count-only request
            # still needs a row, as it always has. The refusal keeps the
            # legacy error and says how to create an empty dataset.
            if attrs.get("number_of_rows") == 0:
                min_rows = self.fields["number_of_rows"].error_messages["min_value"]
                message = f"{min_rows.format(min_value=1)} {ZERO_ROWS_NEED_COLUMNS}"
                raise serializers.ValidationError(
                    {"number_of_rows": message},
                    code="min_value",
                )
            attrs.setdefault("number_of_columns", 1)
            return attrs
        names = [column["name"].casefold() for column in columns]
        if len(names) != len(set(names)):
            raise serializers.ValidationError(
                {"columns": "Column names must be unique."}
            )
        count = attrs.get("number_of_columns", len(columns))
        if count != len(columns):
            raise serializers.ValidationError(
                {"number_of_columns": "Must match the number of supplied columns."}
            )
        attrs["number_of_columns"] = count
        return attrs
