"""Request and response contract for persisted experiment-run comparison."""

from math import isfinite

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.serializer_fields import JsonValueField


class GateIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class ReleaseGateCaseSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    example_id = serializers.UUIDField()
    trial_number = GateIntegerField(min_value=1, max_value=2147483647)


class ReleaseGatePolicySerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    max_regressions = GateIntegerField(default=0, min_value=0)
    critical_cases = serializers.ListField(
        child=ReleaseGateCaseSerializer(), default=list, max_length=10000
    )

    def validate_critical_cases(self, values):
        identities = [(value["example_id"], value["trial_number"]) for value in values]
        if len(identities) != len(set(identities)):
            raise serializers.ValidationError(
                "Critical case identities must be unique."
            )
        return values


class DiffCaseRequestSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField(min_value=1, max_value=2147483647)


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
    include_trial_stability = serializers.BooleanField(default=False)
    release_gate = ReleaseGatePolicySerializer(required=False)
    diff_case = DiffCaseRequestSerializer(required=False)


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


class ReleaseGateBlockerSerializer(serializers.Serializer):
    example_id = serializers.UUIDField(allow_null=True)
    trial_number = serializers.IntegerField(min_value=1, allow_null=True)
    reason = serializers.ChoiceField(
        choices=[
            "missing",
            "duplicate",
            "invalid",
            "error",
            "unknown",
            "unsupported",
            "not_assessed",
            "critical_regression",
            "critical_threshold",
            "regression_limit_exceeded",
            "empty_plan",
        ]
    )


class ReleaseGateReportSerializer(serializers.Serializer):
    verdict = serializers.ChoiceField(choices=["pass", "fail"])
    expected_count = serializers.IntegerField(min_value=0)
    comparable_count = serializers.IntegerField(min_value=0)
    regression_count = serializers.IntegerField(min_value=0)
    policy = ReleaseGatePolicySerializer()
    passing_score = serializers.FloatField(allow_null=True)
    higher_is_better = serializers.BooleanField()
    blockers = ReleaseGateBlockerSerializer(many=True)


class TrialAggregateSerializer(serializers.Serializer):
    recorded_trial_count = serializers.IntegerField(min_value=0)
    missing_trial_count = serializers.IntegerField(min_value=0)
    assessed_trial_count = serializers.IntegerField(min_value=0)
    score_average = serializers.FloatField(allow_null=True)
    score_min = serializers.FloatField(allow_null=True)
    score_max = serializers.FloatField(allow_null=True)
    score_stddev = serializers.FloatField(allow_null=True)


class TrialDeltaAggregateSerializer(serializers.Serializer):
    paired_trial_count = serializers.IntegerField(min_value=0)
    delta_average = serializers.FloatField(allow_null=True)
    delta_stddev = serializers.FloatField(allow_null=True)


class TrialStabilitySerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    expected_trial_count = serializers.IntegerField(min_value=1)
    baseline = TrialAggregateSerializer()
    candidate = TrialAggregateSerializer()
    paired = TrialDeltaAggregateSerializer()


class OutputDiffSideSerializer(serializers.Serializer):
    item_id = serializers.UUIDField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    output_state = serializers.ChoiceField(
        choices=["missing", "unavailable", "over_limit", "invalid", "available"]
    )
    output_digest = serializers.CharField(allow_null=True, allow_blank=True)
    output_bytes = serializers.IntegerField(min_value=0, allow_null=True)
    output_preview = JsonValueField(allow_null=True)
    preview_omitted = serializers.BooleanField()


class OutputDiffComparisonSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["unchanged", "changed", "not_comparable"])
    missing_side = serializers.ChoiceField(
        choices=["baseline", "candidate", "both"], allow_null=True
    )
    changed_paths = serializers.ListField(child=serializers.CharField(), max_length=100)
    changed_path_count = serializers.IntegerField(min_value=0, allow_null=True)
    truncated = serializers.BooleanField()


class CaseOutputDiffSerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField(min_value=1)
    baseline = OutputDiffSideSerializer()
    candidate = OutputDiffSideSerializer()
    output_comparison = OutputDiffComparisonSerializer()


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
    release_gate = ReleaseGateReportSerializer(required=False)
    trial_stability = TrialStabilitySerializer(many=True, required=False)
    case_diff = CaseOutputDiffSerializer(required=False)


class RunComparisonResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RunComparisonResultSerializer()
