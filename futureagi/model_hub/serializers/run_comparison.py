"""Request and response contract for persisted experiment-run comparison."""

from math import isfinite

from rest_framework import serializers


class FiniteScoreField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if not isfinite(value):
            self.fail("invalid")
        return value


class RunComparisonRequestSerializer(serializers.Serializer):
    baseline_run_id = serializers.UUIDField()
    candidate_run_id = serializers.UUIDField()
    metric_id = serializers.UUIDField()
    passing_score = FiniteScoreField(required=False, allow_null=True, default=None)
    higher_is_better = serializers.BooleanField(default=True)


class RunAggregateSerializer(serializers.Serializer):
    item_count = serializers.IntegerField(min_value=0)
    completed_count = serializers.IntegerField(min_value=0)
    failed_count = serializers.IntegerField(min_value=0)
    score_count = serializers.IntegerField(min_value=0)
    score_average = serializers.FloatField(allow_null=True)
    score_min = serializers.FloatField(allow_null=True)
    score_max = serializers.FloatField(allow_null=True)
    pass_count = serializers.IntegerField(min_value=0, allow_null=True)
    pass_rate = serializers.FloatField(allow_null=True)
    status_counts = serializers.DictField(child=serializers.IntegerField(min_value=0))
    score_status_counts = serializers.DictField(
        child=serializers.IntegerField(min_value=0)
    )


class RunCaseComparisonSerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(
        choices=[
            "improved",
            "regressed",
            "tie",
            "missing",
            "duplicate",
            "invalid",
            "error",
            "unknown",
            "unsupported",
            "not_assessed",
        ]
    )
    baseline_status = serializers.CharField(allow_null=True)
    candidate_status = serializers.CharField(allow_null=True)
    baseline_score = serializers.FloatField(allow_null=True)
    candidate_score = serializers.FloatField(allow_null=True)
    score_delta = serializers.FloatField(allow_null=True)
    missing_side = serializers.ChoiceField(
        choices=["baseline", "candidate"], allow_null=True
    )


class RunComparisonSummarySerializer(serializers.Serializer):
    baseline = RunAggregateSerializer()
    candidate = RunAggregateSerializer()
    cases = RunCaseComparisonSerializer(many=True)
    status_counts = serializers.DictField(child=serializers.IntegerField(min_value=0))
    comparable_count = serializers.IntegerField(min_value=0)


class RunCountsSerializer(serializers.Serializer):
    expected = serializers.IntegerField(min_value=0)
    recorded = serializers.IntegerField(min_value=0)
    missing = serializers.IntegerField(min_value=0)
    completed = serializers.IntegerField(min_value=0)
    failed = serializers.IntegerField(min_value=0)
    assessed = serializers.IntegerField(min_value=0)


class RunItemIdentitySerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField(min_value=1)


class MissingRunItemsSerializer(serializers.Serializer):
    baseline = RunItemIdentitySerializer(many=True)
    candidate = RunItemIdentitySerializer(many=True)


class RunTraceReferenceSerializer(serializers.Serializer):
    run_item_id = serializers.UUIDField()
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    root_span_id = serializers.CharField(allow_null=True, allow_blank=True)


class RunCaseTraceReferencesSerializer(RunItemIdentitySerializer):
    baseline = RunTraceReferenceSerializer(allow_null=True)
    candidate = RunTraceReferenceSerializer(allow_null=True)


class RunComparisonResultSerializer(serializers.Serializer):
    baseline_run_id = serializers.UUIDField()
    candidate_run_id = serializers.UUIDField()
    dataset_version_id = serializers.UUIDField()
    metric_id = serializers.UUIDField()
    evaluator_version_id = serializers.UUIDField()
    comparison = RunComparisonSummarySerializer()
    baseline_counts = RunCountsSerializer()
    candidate_counts = RunCountsSerializer()
    missing_by_side = MissingRunItemsSerializer()
    trace_refs = RunCaseTraceReferencesSerializer(many=True)


class RunComparisonResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RunComparisonResultSerializer()
