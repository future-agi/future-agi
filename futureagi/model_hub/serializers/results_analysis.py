"""Exact S1 C-ANALYSIS wire contract; null means unavailable, never zero."""
import json
from uuid import UUID

from rest_framework import serializers
from tfc.utils.api_serializers import StrictInputSerializer


class ResultsAnalysisQuerySerializer(StrictInputSerializer):
    runs = serializers.CharField(max_length=8 * 37)
    baseline = serializers.CharField(required=False, max_length=36)
    cand = serializers.UUIDField(required=False)
    filters = serializers.CharField(required=False, max_length=16384)
    group_by = serializers.ChoiceField(choices=["none"], default="none")
    bmetric = serializers.CharField(required=False, max_length=128)
    wmetric = serializers.CharField(required=False, max_length=128)
    include = serializers.ChoiceField(choices=["root_causes"], required=False)

    def validate_runs(self, value):
        try:
            values = [str(UUID(part)) for part in value.split(",")]
        except (ValueError, TypeError, AttributeError) as exc:
            raise serializers.ValidationError("runs must contain UUIDs.") from exc
        if not 1 <= len(values) <= 8 or len(values) != len(set(values)):
            raise serializers.ValidationError("Select 1-8 distinct runs.")
        return values

    def validate_baseline(self, value):
        if value == "none":
            return value
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise serializers.ValidationError("baseline must be a UUID or none.") from exc

    def validate_filters(self, value):
        def invalid_constant(_value):
            raise ValueError("Non-finite JSON is not supported.")
        try:
            values = json.loads(value, parse_constant=invalid_constant)
        except (ValueError, RecursionError) as exc:
            raise serializers.ValidationError("filters must be valid JSON.") from exc
        if not isinstance(values, list) or len(values) > 50:
            raise serializers.ValidationError("filters must be an array of at most 50 pills.")
        if any(not isinstance(p, dict) or not isinstance(p.get("type"), str) for p in values):
            raise serializers.ValidationError("Each filter requires a type.")
        # Semantic validation belongs to P03's shared selector, not a second dialect.
        return values


class AvailabilitySerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["available", "unavailable", "incompatible"])
    reason = serializers.CharField(allow_null=True)


class AnalysisRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField()
    experiment_id = serializers.UUIDField(allow_null=True)
    dataset_version_id = serializers.UUIDField()
    task_kind = serializers.CharField()
    label = serializers.CharField()
    name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    planned_trials = serializers.IntegerField(min_value=0)
    recorded_trials = serializers.IntegerField(min_value=0)
    missing_trials = serializers.IntegerField(min_value=0)


class AnalysisValueSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    value = serializers.FloatField(allow_null=True)
    n = serializers.IntegerField(min_value=0)
    expected_n = serializers.IntegerField(min_value=0)
    missing_n = serializers.IntegerField(min_value=0)
    evaluator_version_id = serializers.UUIDField(allow_null=True)
    unavailable_reason = serializers.CharField(allow_null=True)
    ci95 = serializers.ListField(child=serializers.FloatField(), min_length=2,
                                 max_length=2, allow_null=True)
    score_status_counts = serializers.DictField(child=serializers.IntegerField(min_value=0))


class AnalysisComparisonSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    baseline_run_id = serializers.UUIDField()
    delta = serializers.FloatField(allow_null=True)
    n = serializers.IntegerField(min_value=0)
    expected_n = serializers.IntegerField(min_value=0)
    ci95 = serializers.ListField(child=serializers.FloatField(), min_length=2,
                                 max_length=2, allow_null=True)
    p_value = serializers.FloatField(allow_null=True)
    significant = serializers.BooleanField(allow_null=True)
    availability = AvailabilitySerializer()


class AnalysisMetricSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    family = serializers.ChoiceField(choices=["eval"])
    aggregation = serializers.ChoiceField(choices=["assessed_trial_mean"])
    denominator_unit = serializers.ChoiceField(choices=["example_trial"])
    higher_is_better = serializers.BooleanField(allow_null=True)
    pass_threshold = serializers.FloatField(allow_null=True)
    per_run = AnalysisValueSerializer(many=True)
    vs_baseline = AnalysisComparisonSerializer(many=True)


class AnalysisVerdictSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    baseline_run_id = serializers.UUIDField()
    metric = serializers.CharField()
    status = serializers.ChoiceField(choices=["observed_difference", "unavailable"])
    delta = serializers.FloatField(allow_null=True)
    n = serializers.IntegerField(min_value=0)
    reason = serializers.CharField()
    significant = serializers.BooleanField(allow_null=True)


class ObservedFailureSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    metric = serializers.CharField()
    failed_trials = serializers.IntegerField(min_value=0)
    assessed_trials = serializers.IntegerField(min_value=0)
    example_ids = serializers.ListField(child=serializers.UUIDField(), max_length=50)
    examples_total = serializers.IntegerField(min_value=0)
    examples_has_more = serializers.BooleanField()
    causal = serializers.BooleanField()


class ObservedFailuresSerializer(serializers.Serializer):
    groups = ObservedFailureSerializer(many=True)
    total = serializers.IntegerField(min_value=0)
    has_more = serializers.BooleanField()
    availability = AvailabilitySerializer()


class NullEvidenceField(serializers.Field):
    """Reserved optional evidence is explicitly null in this S1 contract."""
    def __init__(self, **kwargs):
        super().__init__(allow_null=True, **kwargs)

    def to_internal_value(self, data):
        raise serializers.ValidationError("This S1 field must be null.")

    def to_representation(self, value):
        if value is not None:
            raise ValueError("Unavailable S1 evidence must be null")
        return None


class AnalysisScopeSerializer(serializers.Serializer):
    population = serializers.ChoiceField(choices=["full_filtered_plan"])
    complete = serializers.BooleanField()


class ResultsAnalysisResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    running = serializers.BooleanField()
    runs = AnalysisRunSerializer(many=True)
    metrics = AnalysisMetricSerializer(many=True)
    verdicts = AnalysisVerdictSerializer(many=True)
    baseline_run_id = serializers.UUIDField(allow_null=True)
    baseline_label = serializers.CharField(allow_null=True)
    candidate_run_id = serializers.UUIDField(allow_null=True)
    examples_in_view = serializers.IntegerField(min_value=0)
    examples_total = serializers.IntegerField(min_value=0)
    scope = AnalysisScopeSerializer()
    default_evals = serializers.ListField(child=serializers.CharField())
    preference = NullEvidenceField()
    human = NullEvidenceField()
    agent = NullEvidenceField()
    worklist = NullEvidenceField()
    breakdown = NullEvidenceField()
    breakdown_options = serializers.ListField(child=serializers.CharField())
    pattern = NullEvidenceField()
    root_causes = NullEvidenceField()
    observed_failures = ObservedFailuresSerializer(allow_null=True)
    availability = serializers.DictField(child=AvailabilitySerializer())


class ResultsAnalysisResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResultsAnalysisResultSerializer()
