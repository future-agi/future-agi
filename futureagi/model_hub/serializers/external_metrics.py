"""Contracts for external metrics: dataset metrics the caller scores (SRV-5)."""

from rest_framework import serializers

from model_hub.serializers.run_comparison import FiniteScoreField
from model_hub.services.external_metrics import OUTPUT_TYPES
from tfc.utils.api_serializers import StrictInputSerializer


class ExternalMetricCreateRequestSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    name = serializers.CharField(max_length=255)
    output_type = serializers.ChoiceField(choices=list(OUTPUT_TYPES))
    higher_is_better = serializers.BooleanField(default=True)
    pass_threshold = FiniteScoreField(required=False, allow_null=True, default=None)
    source_hash = serializers.RegexField(
        r"^[0-9a-f]{64}$",
        help_text="Lowercase sha256 hex digest of the evaluator source. The source itself is never sent or stored.",
    )

    def validate_name(self, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise serializers.ValidationError(
                "Name must not contain control characters."
            )
        return value

    def validate(self, attrs):
        threshold = attrs.get("pass_threshold")
        if (
            threshold is not None
            and attrs["output_type"] != "numeric"
            and not 0.0 <= threshold <= 1.0
        ):
            raise serializers.ValidationError(
                {
                    "pass_threshold": "Must be between 0 and 1 for pass_fail and percentage."
                }
            )
        return attrs


class ExternalMetricSerializer(serializers.Serializer):
    metric_id = serializers.UUIDField()
    evaluator_version_id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    version_number = serializers.IntegerField()
    dataset_id = serializers.UUIDField()
    name = serializers.CharField()
    output_type = serializers.ChoiceField(choices=list(OUTPUT_TYPES))
    higher_is_better = serializers.BooleanField()
    pass_threshold = serializers.FloatField(allow_null=True)
    source_hash = serializers.CharField()
    created_at = serializers.DateTimeField()


class ExternalMetricCreateResultSerializer(ExternalMetricSerializer):
    created = serializers.BooleanField(
        help_text="True when this request created the metric; false when it reused one."
    )


class ExternalMetricCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExternalMetricCreateResultSerializer()


class ExternalMetricListQuerySerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=255)
    limit = serializers.IntegerField(default=100, min_value=1, max_value=500)
    offset = serializers.IntegerField(default=0, min_value=0, max_value=2147483647)


class ExternalMetricListResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    metrics = ExternalMetricSerializer(many=True)
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class ExternalMetricListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExternalMetricListResultSerializer()
