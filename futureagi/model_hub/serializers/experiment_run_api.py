"""Bounded external-run creation and result-ingestion contracts."""

from rest_framework import serializers

from model_hub.serializers.run_comparison import FiniteScoreField
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
    metadata = RunJsonObjectField(default=dict)

    def validate_evaluator_metric_ids(self, values):
        if len(values) != len(set(values)):
            raise serializers.ValidationError("Evaluator metric IDs must be unique.")
        return values

    def validate_metadata(self, value):
        if "_futureagi_run_write" in value:
            raise serializers.ValidationError("Reserved run metadata is not writable.")
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
    metadata = RunJsonObjectField()
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
