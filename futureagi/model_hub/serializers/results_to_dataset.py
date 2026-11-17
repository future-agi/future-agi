"""Request and response contracts for copying selected canonical results."""
from rest_framework import serializers


class ResultsToDatasetRequestSerializer(serializers.Serializer):
    source_dataset_id = serializers.UUIDField()
    run_id = serializers.UUIDField()
    example_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=1000,
        help_text="Distinct canonical example IDs, in the order to append. Not result or row IDs.",
    )
    trial = serializers.IntegerField(default=1, min_value=1, max_value=10000)
    mode = serializers.ChoiceField(choices=["copy"], default="copy")
    new_dataset_name = serializers.CharField(
        required=False, default="", allow_blank=True, max_length=2000,
        help_text="Required for target new; omitted for an existing dataset.",
    )
    column_map = serializers.DictField(
        child=serializers.CharField(max_length=2000, allow_null=True), required=False,
        help_text=(
            "Source selector to existing target column UUID or, for target new, new column name. "
            "Selectors: inputs (whole JSON), input:<literal top-level key>, output, expected, "
            "score:<metric UUID>, or a source column UUID (native snapshot input.values). "
            "Null explicitly skips a source. Destinations must be distinct. Max 100 entries. "
            "Required for existing targets. New targets default to inputs/output/expected "
            "and score_columns, stored as JSON columns."
        ),
    )
    score_columns = serializers.ListField(
        child=serializers.UUIDField(), default=list, max_length=100,
        help_text="Chosen pinned metrics. With column_map, each must have a non-null score:<UUID> mapping.",
    )
    expected_from = serializers.ChoiceField(choices=["keep", "empty", "output"], default="keep")
    needs_review = serializers.BooleanField(default=False)
    expected_source = serializers.ChoiceField(choices=["production_output"], required=False)
    version_description = serializers.CharField(
        default="", allow_blank=True, max_length=2000,
        help_text="Recorded as the import note on copied rows; does not create a dataset version.",
    )
    split_ids = serializers.ListField(child=serializers.UUIDField(), default=list, max_length=0)
    new_splits = serializers.ListField(child=serializers.DictField(), default=list, max_length=0)
    save_version = serializers.BooleanField(default=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected a JSON object.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(dict.fromkeys(unknown, "Unknown field."))
        return super().to_internal_value(data)

    def validate_example_ids(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError("Select each result row only once.")
        return values

    def validate_score_columns(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError("Select each score column only once.")
        return values

    def validate_column_map(self, value):
        if not 1 <= len(value) <= 100 or not any(v is not None for v in value.values()):
            raise serializers.ValidationError("Map between 1 and 100 columns.")
        destinations = [v for v in value.values() if v is not None]
        if len(destinations) != len(set(destinations)):
            raise serializers.ValidationError("Map each destination column only once.")
        if any(not key or len(key) > 2000 for key in value):
            raise serializers.ValidationError("Invalid source column selector.")
        return value

    def validate(self, attrs):
        if attrs["save_version"]:
            raise serializers.ValidationError({
                "save_version": "Copy rows without saving a version. Use the dataset version action separately."
            })
        return attrs


class ResultImportFailureSerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    code = serializers.ChoiceField(choices=[
        "example_unavailable", "result_unavailable", "output_unavailable",
        "score_unavailable", "input_unavailable", "invalid_result", "invalid_value",
    ])
    message = serializers.CharField()


class ResultImportRowSerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    result_id = serializers.UUIDField()
    row_id = serializers.UUIDField()


class ResultsToDatasetResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField(allow_null=True)
    dataset_name = serializers.CharField(allow_null=True)
    dataset_url = serializers.CharField(allow_null=True)
    created = serializers.BooleanField()
    outcome = serializers.ChoiceField(choices=["completed", "partial", "failed"])
    rows_requested = serializers.IntegerField(min_value=1)
    rows_added = serializers.IntegerField(min_value=0)
    rows_failed = serializers.IntegerField(min_value=0)
    trial = serializers.IntegerField(min_value=1)
    column_map = serializers.DictField(child=serializers.UUIDField())
    skipped_columns = serializers.ListField(child=serializers.CharField())
    rows = ResultImportRowSerializer(many=True)
    failures = ResultImportFailureSerializer(many=True)


class ResultsToDatasetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResultsToDatasetResultSerializer()


class ResultsToDatasetErrorSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    type = serializers.CharField()
    code = serializers.CharField()
    detail = serializers.CharField()
    message = serializers.CharField()
    error = serializers.CharField()
    result = serializers.CharField()
    attr = serializers.CharField(required=False)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()), required=False,
    )


class ResultsToDatasetAllFailedSerializer(ResultsToDatasetErrorSerializer):
    result = ResultsToDatasetResultSerializer()
