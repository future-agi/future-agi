"""Validated inputs for immutable experiment row selection."""

from rest_framework import serializers

from model_hub.services.run_metadata_public import (
    public_run_metadata,
    reject_reserved_metadata_keys,
)


class ExperimentSplitRunScopeSerializer(serializers.Serializer):
    """F50-only readback while F05 is OFF; never advertises picked rows/sample."""

    split_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True)
    selected_row_ids = serializers.ListField(
        child=serializers.UUIDField(), read_only=True
    )
    source_count = serializers.IntegerField(read_only=True)
    eligible_count = serializers.IntegerField(read_only=True)
    selected_count = serializers.IntegerField(read_only=True)


class ExperimentSampleSerializer(serializers.Serializer):
    size = serializers.IntegerField(min_value=1, max_value=100000)
    seed = serializers.IntegerField(min_value=0, max_value=2147483647)

    class Meta:
        swagger_schema_fields = {
            "description": (
                "Experiment run settings are on by default. When a server turns them "
                "off, a create request that sends this field is refused (HTTP 400) and "
                "experiment detail responses leave it out."
            ),
        }

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - {"size", "seed"}:
            raise serializers.ValidationError("Unknown sample setting.")
        return super().to_internal_value(data)


class ExperimentLimitsSerializer(serializers.Serializer):
    tokens = serializers.IntegerField(min_value=1, max_value=10000000, required=False)
    messages = serializers.IntegerField(min_value=1, max_value=100000, required=False)
    cost_usd = serializers.FloatField(
        min_value=0.000001, max_value=100000, required=False
    )
    timeout_s = serializers.IntegerField(min_value=1, max_value=43200, required=False)

    class Meta:
        swagger_schema_fields = {
            "description": (
                "Per-row limits. This release accepts only timeout_s, which is checked "
                "before and after each model call, not during one; tokens, messages and "
                "cost_usd are refused (HTTP 400). Experiment run settings are on by "
                "default. When a server turns them off, a create request that sends "
                "this field is refused (HTTP 400) and experiment detail responses leave "
                "it out."
            ),
        }

    def to_internal_value(self, data):
        import math

        if isinstance(data, dict) and set(data) - {
            "tokens",
            "messages",
            "cost_usd",
            "timeout_s",
        }:
            raise serializers.ValidationError("Unknown per-row limit.")
        result = super().to_internal_value(data)
        if "cost_usd" in result and not math.isfinite(result["cost_usd"]):
            raise serializers.ValidationError("Cost limit must be finite.")
        return result


class RunMetadataField(serializers.DictField):
    """Shared public boundary for stored run and experiment metadata."""

    def to_internal_value(self, data):
        reject_reserved_metadata_keys({"metadata": data})
        return super().to_internal_value(data)

    def to_representation(self, value):
        return super().to_representation(public_run_metadata(value))


class ExperimentMetadataField(RunMetadataField):
    def __init__(self, **kwargs):
        super().__init__(
            child=serializers.CharField(max_length=1000, allow_blank=True), **kwargs
        )

    def to_internal_value(self, data):
        result = super().to_internal_value(data)
        if len(result) > 50 or any(not key.strip() or len(key) > 100 for key in result):
            raise serializers.ValidationError(
                "Use at most 50 metadata entries with nonempty keys of at most 100 characters."
            )
        return result
