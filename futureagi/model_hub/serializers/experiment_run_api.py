"""Bounded external-run creation and result-ingestion contracts."""

from rest_framework import serializers

from model_hub.serializers.run_comparison import FiniteScoreField
from model_hub.services.run_metadata_public import (
    public_run_metadata,
    reject_reserved_metadata_keys,
)
from model_hub.services.experiment_run_writes import RunWriteError, _canonical
from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.serializer_fields import JsonValueField


class RunJsonValueField(JsonValueField):
    """Keep open-shape outputs/configuration strict JSON and bounded in bytes."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            encoded = _canonical(value)
        except RunWriteError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if len(encoded.encode("utf-8")) > 262144:
            raise serializers.ValidationError(
                "JSON field must not exceed 262144 bytes."
            )
        return value


class RunJsonObjectField(RunJsonValueField):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": {"x-json-value": True},
        }

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected a JSON object.")
        return value


class RunMetadataObjectField(RunJsonObjectField):
    def to_internal_value(self, data):
        reject_reserved_metadata_keys({"metadata": data})
        return super().to_internal_value(data)

    def to_representation(self, value):
        return super().to_representation(public_run_metadata(value))


class ExternalRunIdentitySerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField(
        default=1, min_value=1, max_value=2147483647
    )


class ExternalRunCreateRequestSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    run_id = serializers.UUIDField()
    dataset_version_id = serializers.UUIDField()
    expected_items = serializers.ListField(
        child=ExternalRunIdentitySerializer(), min_length=1, max_length=10000
    )
    evaluator_metric_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=100, default=list
    )
    experiment_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    model_config = RunJsonObjectField(default=dict)
    metadata = RunMetadataObjectField(default=dict)

    def to_internal_value(self, data):
        reject_reserved_metadata_keys(data)
        return super().to_internal_value(data)

    def validate_evaluator_metric_ids(self, values):
        if len(values) != len(set(values)):
            raise serializers.ValidationError("Evaluator metric IDs must be unique.")
        return values

    def validate_metadata(self, value):
        reject_reserved_metadata_keys({"metadata": value})
        return value


class ExternalRunBindingSerializer(serializers.Serializer):
    metric_id = serializers.UUIDField()
    evaluator_version_id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    version_number = serializers.IntegerField()
    binding_config = JsonValueField(allow_null=True)
    binding_model = serializers.CharField(allow_null=True, allow_blank=True)
    prompt_messages = JsonValueField(allow_null=True)
    config_snapshot = JsonValueField(allow_null=True)
    criteria = serializers.CharField(allow_null=True, allow_blank=True)
    model = serializers.CharField(allow_null=True, allow_blank=True)
    output_type_normalized = serializers.CharField(allow_null=True, allow_blank=True)
    pass_threshold = serializers.FloatField(allow_null=True)
    choice_scores = JsonValueField(allow_null=True)


class ExternalRunHeaderSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    ordinal = serializers.IntegerField(
        allow_null=True,
        help_text="1, 2, 3 ... per dataset in creation order; never reused.",
    )
    label = serializers.CharField(allow_null=True, help_text='"#" + ordinal.')
    app_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "Link to this run in the app: the dataset's Experiments tab, "
            "whose run= parameter opens the run's panel with its traces, "
            "joined by run id. Null when the deployment sets no frontend URL."
        ),
    )
    dataset_version_id = serializers.UUIDField()
    experiment_id = serializers.UUIDField(allow_null=True)
    task_kind = serializers.ChoiceField(choices=["external"])
    status = serializers.ChoiceField(
        choices=[
            "draft",
            "queued",
            "running",
            "partial",
            "completed",
            "failed",
            "cancelled",
        ]
    )
    expected_items = ExternalRunIdentitySerializer(many=True)
    evaluator_bindings = ExternalRunBindingSerializer(many=True)
    model_config = RunJsonObjectField()
    metadata = RunMetadataObjectField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ExternalRunCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExternalRunHeaderSerializer()


class ExternalRunScoreSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    metric_id = serializers.UUIDField()
    evaluator_version_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=["assessed", "error", "unknown", "not_assessed", "unsupported"]
    )
    value = FiniteScoreField(required=False, allow_null=True)
    explanation = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=16384
    )
    trace_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=255
    )
    span_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=255
    )

    def validate(self, attrs):
        if attrs["status"] == "assessed" and attrs.get("value") is None:
            raise serializers.ValidationError(
                {"value": "Assessed evidence requires a finite value."}
            )
        return attrs


class ExternalRunResultSerializer(ExternalRunIdentitySerializer):
    receipt_key = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=["completed", "failed", "skipped"])
    output = RunJsonValueField(required=False, allow_null=True, default=None)
    error = RunJsonValueField(required=False, allow_null=True, default=None)
    trace_id = serializers.CharField(
        required=False, allow_null=True, max_length=255, default=None
    )
    root_span_id = serializers.CharField(
        required=False, allow_null=True, max_length=255, default=None
    )
    latency_ms = FiniteScoreField(
        required=False, allow_null=True, min_value=0, default=None
    )
    token_usage = RunJsonValueField(required=False, allow_null=True, default=None)
    cost = FiniteScoreField(required=False, allow_null=True, min_value=0, default=None)
    score_refs = serializers.ListField(
        child=ExternalRunScoreSerializer(), required=False, default=list, max_length=100
    )

    def validate(self, attrs):
        if attrs["status"] == "completed" and attrs["error"] is not None:
            raise serializers.ValidationError(
                {"error": "Completed result cannot contain an error."}
            )
        refs = attrs["score_refs"]
        pairs = [(ref["metric_id"], ref["evaluator_version_id"]) for ref in refs]
        if len(pairs) != len(set(pairs)):
            raise serializers.ValidationError(
                {"score_refs": "Score bindings must be unique."}
            )
        return attrs


class ExternalRunResultsRequestSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    def to_internal_value(self, data):
        reject_reserved_metadata_keys(data)
        try:
            encoded = _canonical(data)
        except RunWriteError as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc
        if len(encoded.encode("utf-8")) > 2097152:
            raise serializers.ValidationError(
                {"non_field_errors": ["Result batch must not exceed 2097152 bytes."]}
            )
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            if any(
                len(_canonical(item).encode("utf-8")) > 1048576
                for item in data["items"]
            ):
                raise serializers.ValidationError(
                    {"items": ["Result item must not exceed 1048576 bytes."]}
                )
        return super().to_internal_value(data)

    items = serializers.ListField(
        child=ExternalRunResultSerializer(), min_length=1, max_length=100
    )

    def validate_items(self, items):
        identities = [(item["example_id"], item["trial_number"]) for item in items]
        if len(identities) != len(set(identities)):
            raise serializers.ValidationError(
                "Result identities must be unique within a batch."
            )
        return items


class ExternalRunItemReceiptSerializer(ExternalRunIdentitySerializer):
    id = serializers.UUIDField()
    receipt_key = serializers.CharField()
    status = serializers.ChoiceField(choices=["completed", "failed", "skipped"])
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    root_span_id = serializers.CharField(allow_null=True, allow_blank=True)
    score_refs = ExternalRunScoreSerializer(many=True)


class ExternalRunResultsResultSerializer(serializers.Serializer):
    run = ExternalRunHeaderSerializer()
    items = ExternalRunItemReceiptSerializer(many=True)


class ExternalRunResultsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExternalRunResultsResultSerializer()


# --- SRV-3: read a run and its recorded items ---------------------------------


class ExperimentRunItemCountsSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    running = serializers.IntegerField()
    completed = serializers.IntegerField()
    failed = serializers.IntegerField()
    skipped = serializers.IntegerField()
    not_started = serializers.IntegerField(
        help_text="Planned identities with no recorded item yet."
    )


class ExperimentRunReadSerializer(ExternalRunHeaderSerializer):
    task_kind = serializers.ChoiceField(choices=["prompt", "graph", "external"])
    plan_size = serializers.IntegerField()
    item_counts = ExperimentRunItemCountsSerializer()


class ExperimentRunReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentRunReadSerializer()


class ExperimentRunItemsQuerySerializer(serializers.Serializer):
    status = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=64,
        help_text="Comma-separated item statuses: pending, running, completed, failed, skipped.",
    )
    limit = serializers.IntegerField(default=100, min_value=1, max_value=500)
    offset = serializers.IntegerField(default=0, min_value=0, max_value=2147483647)
    max_value_bytes = serializers.IntegerField(
        default=4096,
        min_value=0,
        max_value=262144,
        help_text="Per-item bound for output and error; larger values return a flagged prefix.",
    )

    def validate_status(self, value):
        from model_hub.services.experiment_run_read import ITEM_STATUSES

        statuses = [part.strip() for part in value.split(",") if part.strip()]
        unknown = sorted(set(statuses) - set(ITEM_STATUSES))
        if unknown:
            raise serializers.ValidationError(
                f"Unknown item status: {', '.join(unknown)}."
            )
        return statuses

    def validate(self, attrs):
        from model_hub.services.experiment_run_read import MAX_PAGE_VALUE_BYTES

        if attrs["limit"] * attrs["max_value_bytes"] > MAX_PAGE_VALUE_BYTES:
            raise serializers.ValidationError(
                {
                    "max_value_bytes": (
                        f"limit * max_value_bytes must not exceed {MAX_PAGE_VALUE_BYTES}."
                    )
                }
            )
        return attrs


class ExperimentRunScoreReadSerializer(serializers.Serializer):
    metric_id = serializers.UUIDField(allow_null=True)
    evaluator_version_id = serializers.UUIDField(allow_null=True)
    status = serializers.CharField()
    value = serializers.FloatField(allow_null=True)
    explanation = serializers.CharField(allow_null=True, allow_blank=True)
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    span_id = serializers.CharField(allow_null=True, allow_blank=True)


class ExperimentRunItemReadSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField()
    status = serializers.ChoiceField(
        choices=["pending", "running", "completed", "failed", "skipped"]
    )
    receipt_key = serializers.CharField(allow_null=True)
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    root_span_id = serializers.CharField(allow_null=True, allow_blank=True)
    latency_ms = serializers.FloatField(allow_null=True)
    cost = serializers.FloatField(allow_null=True)
    token_usage = JsonValueField(allow_null=True)
    score_refs = ExperimentRunScoreReadSerializer(many=True)
    output = JsonValueField(
        allow_null=True,
        help_text="The stored JSON value, or a canonical-JSON prefix string when output_truncated.",
    )
    output_truncated = serializers.BooleanField()
    output_bytes = serializers.IntegerField()
    error = JsonValueField(
        allow_null=True,
        help_text="The stored JSON value, or a canonical-JSON prefix string when error_truncated.",
    )
    error_truncated = serializers.BooleanField()
    error_bytes = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ExperimentRunItemsResultSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    run_status = serializers.CharField()
    items = ExperimentRunItemReadSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class ExperimentRunItemsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentRunItemsResultSerializer()
