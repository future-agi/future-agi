"""Strict R1 query and canonical run response contracts (separate from SDK R1)."""
from uuid import UUID

from rest_framework import serializers


class StrictQuerySerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unknown query parameter." for key in unknown})
        if hasattr(data, "getlist") and any(len(data.getlist(k)) != 1 for k in data):
            raise serializers.ValidationError("Repeated query parameters are not supported.")
        return super().to_internal_value(data)


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise serializers.ValidationError("Expected a UUID.") from exc


class RunSelectorField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return value if value == "none" else identifier(value)


class ResultsRunsQuerySerializer(StrictQuerySerializer):
    include = serializers.CharField(default="", allow_blank=True, max_length=128)
    status = serializers.CharField(default="", allow_blank=True, max_length=128)
    baseline = RunSelectorField(required=False, max_length=36)
    dataset_version_id = serializers.CharField(default="all", max_length=36)
    same_target_as = serializers.UUIDField(required=False)
    experiment_id = serializers.UUIDField(required=False)
    search = serializers.CharField(default="", allow_blank=True, max_length=200)
    limit = serializers.IntegerField(default=500, min_value=1, max_value=500)
    offset = serializers.IntegerField(default=0, min_value=0, max_value=1000000)

    def validate_include(self, value):
        parts = value.split(",") if value else []
        if len(set(parts)) != len(parts) or set(parts) - {"metrics", "config", "lineage", "quick_tests"}:
            raise serializers.ValidationError("Unsupported include token.")
        return parts

    def validate_status(self, value):
        parts = value.split(",") if value else []
        if set(parts) - {"draft", "queued", "running", "partial", "completed", "failed", "cancelled"}:
            raise serializers.ValidationError("Unsupported run status.")
        return parts

    def validate_dataset_version_id(self, value):
        return value if value == "all" else identifier(value)


class CanonicalRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField()
    experiment_id = serializers.UUIDField(allow_null=True)
    experiment_name = serializers.CharField(allow_null=True, allow_blank=True)
    experiment_description = serializers.CharField(allow_null=True, allow_blank=True)
    dataset_version_id = serializers.UUIDField()
    comparison_group = serializers.CharField(required=False, allow_null=True)
    dataset_version = serializers.JSONField()
    task_kind = serializers.CharField()
    ordinal = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True, allow_blank=True)
    status = serializers.CharField()
    final = serializers.BooleanField()
    listed = serializers.BooleanField()
    kind = serializers.CharField()
    trials = serializers.IntegerField(allow_null=True)
    concurrency = serializers.IntegerField(allow_null=True)
    trial_reducer = serializers.JSONField(allow_null=True)
    error_policy = serializers.JSONField(allow_null=True)
    requested_settings = serializers.JSONField(allow_null=True)
    effective_settings = serializers.JSONField(allow_null=True)
    availability = serializers.JSONField()
    counts = serializers.JSONField()
    progress = serializers.JSONField()
    metrics = serializers.JSONField()
    eval_versions = serializers.ListField(child=serializers.DictField())
    config = serializers.JSONField(required=False, allow_null=True)
    source = serializers.JSONField(required=False, allow_null=True)
    output_column_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_config_id = serializers.UUIDField(required=False, allow_null=True)
    agent_config_id = serializers.UUIDField(required=False, allow_null=True)
    splits = serializers.JSONField(required=False, allow_null=True)
    sample = serializers.JSONField(required=False, allow_null=True)
    parent_run_id = serializers.UUIDField(required=False, allow_null=True)
    iteration = serializers.JSONField(required=False, allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class ResultsRunsResultSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    pinned_run_id = serializers.UUIDField(allow_null=True)
    baseline_run_id = serializers.UUIDField(allow_null=True)
    baseline = serializers.DictField()
    metric_defs = serializers.ListField(child=serializers.DictField())
    results = CanonicalRunSerializer(many=True)
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()
    next = serializers.IntegerField(allow_null=True)
    scope = serializers.DictField()


class ResultsRunsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResultsRunsResultSerializer()
