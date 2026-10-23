"""Explicit wire contracts for sampling policies and previews."""

from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
from tracer.serializers.filters import StrictInputSerializer, eval_task_filters_field


class SamplingAttributeSerializer(StrictInputSerializer):
    source = serializers.ChoiceField(
        choices=("span", "resource", "metadata"), default="span"
    )
    key = serializers.CharField(max_length=200)


class SamplingPredicateSerializer(SamplingAttributeSerializer):
    op = serializers.ChoiceField(
        choices=("in", "not_in", "exists", "missing", "gt", "gte", "lt", "lte"),
        default="in",
    )
    values = serializers.ListField(
        child=JsonValueField(), max_length=100, required=False
    )


class SamplingRuleSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=80, required=False)
    filters = SamplingPredicateSerializer(many=True, required=False)
    rate = serializers.FloatField(
        min_value=0,
        max_value=100,
        required=False,
        help_text="Percentage with at most six decimal places; closed quotas use exact integer arithmetic.",
    )
    target_n = serializers.IntegerField(min_value=0, max_value=1000000, required=False)
    min_n = serializers.IntegerField(min_value=0, max_value=1000000, required=False)
    max_n = serializers.IntegerField(min_value=0, max_value=1000000, required=False)
    rare_below = serializers.IntegerField(
        min_value=0, max_value=1000000, required=False
    )


class SamplingWindowSerializer(StrictInputSerializer):
    start = serializers.CharField(help_text="Inclusive ISO 8601 UTC event time.")
    end = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Closed mode: exclusive UTC end, at most 31 days. Live mode: null (ongoing).",
    )


class SamplingPolicySerializer(StrictInputSerializer):
    version = serializers.IntegerField(min_value=1, max_value=1)
    sampling_unit = serializers.ChoiceField(
        choices=("span", "trace", "session", "user", "attribute")
    )
    unit_attribute = SamplingAttributeSerializer(required=False)
    missing_id = serializers.ChoiceField(choices=("exclude", "row"), default="exclude")
    mode = serializers.ChoiceField(choices=("closed", "live"), default="closed")
    window = SamplingWindowSerializer()
    strata = SamplingRuleSerializer(many=True, required=False)
    group_by = SamplingAttributeSerializer(many=True, required=False)
    default = SamplingRuleSerializer(required=False)
    # No defaults: an omitted key is the historical independent policy, and
    # unchanged policies must round-trip byte for byte on edit.
    coordination = serializers.ChoiceField(
        choices=("independent", "project"),
        required=False,
        help_text="independent (default): this policy draws its own sample. project: every coordinated policy of the project (and panel) shares one permanent random number per unit, so samples nest as rates or quotas grow and overlap across tasks.",
    )
    panel = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text="Project coordination only: a named PRN panel; different panels draw independent numbers.",
    )
    prn_start = serializers.FloatField(
        min_value=0,
        max_value=100,
        required=False,
        help_text="Project coordination only: start point (percent of the hash circle, below 100, at most six decimals). Disjoint start intervals give disjoint live samples.",
    )


class SamplingPreviewRequestSerializer(StrictInputSerializer):
    project = serializers.UUIDField()
    row_type = serializers.ChoiceField(
        choices=("spans", "traces", "sessions"), default="spans"
    )
    filters = eval_task_filters_field(required=False, allow_null=True)
    sampling_policy = SamplingPolicySerializer()
    evals = serializers.ListField(
        child=serializers.UUIDField(), max_length=50, required=False, default=list
    )


class SamplingPreviewCellSerializer(serializers.Serializer):
    stratum = serializers.CharField()
    eligible_units = serializers.IntegerField()
    eligible_rows = serializers.IntegerField()
    expected_units = serializers.FloatField()
    expected_rows = serializers.FloatField()
    selected_units = serializers.IntegerField()
    selected_rows = serializers.IntegerField()


class SamplingPreviewResultSerializer(serializers.Serializer):
    policy_version = serializers.CharField()
    sampling_unit = serializers.CharField()
    mode = serializers.CharField()
    window = SamplingWindowSerializer()
    preview_window = SamplingWindowSerializer()
    as_of = serializers.DateTimeField()
    strata = SamplingPreviewCellSerializer(many=True)
    evaluator_count = serializers.IntegerField(min_value=0)
    expected_evaluations = serializers.FloatField(allow_null=True)
    selected_evaluations = serializers.IntegerField(allow_null=True)
    estimated_cost = serializers.FloatField(allow_null=True)
    cost_note = serializers.CharField()
    population_note = serializers.CharField()


class SamplingPreviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SamplingPreviewResultSerializer()


class SamplingAuditQuerySerializer(StrictInputSerializer):
    eval_task_id = serializers.UUIDField()
    cohort_id = serializers.UUIDField(
        required=False,
        help_text="Audit this cohort of the task (for example an archived one) instead of the current policy's cohort.",
    )


class SamplingAuditStratumSerializer(serializers.Serializer):
    stratum = serializers.CharField()
    rule = serializers.CharField()
    population_size = serializers.IntegerField(
        help_text="Closed: units in the stratum at the freeze. Live: units decided since the policy start."
    )
    target = serializers.IntegerField(
        allow_null=True,
        help_text="Closed: the quota the rule asks for before the population cap.",
    )
    expected_size = serializers.FloatField(
        allow_null=True,
        help_text="Closed: the exact quota k = min(N, target). Live: N times the rate.",
    )
    achieved = serializers.IntegerField()
    shortage = serializers.IntegerField(allow_null=True)
    exact = serializers.BooleanField(allow_null=True)
    inclusion_probability = serializers.FloatField()
    design_weight = serializers.FloatField(allow_null=True)
    srm_z = serializers.FloatField(allow_null=True)
    srm_p_value = serializers.FloatField(allow_null=True)
    srm_flagged = serializers.BooleanField(allow_null=True)
    population_check = serializers.ChoiceField(
        choices=("match", "changed", "persisted")
    )


class SamplingAuditSummarySerializer(serializers.Serializer):
    strata = serializers.IntegerField()
    population_size = serializers.IntegerField()
    achieved = serializers.IntegerField()
    kish_effective_n = serializers.FloatField(allow_null=True)
    design_effect = serializers.FloatField(allow_null=True)
    weight_cv = serializers.FloatField(allow_null=True)
    weighted_population = serializers.FloatField(
        help_text="Sum of design weights of the sampled units."
    )
    represented_population = serializers.IntegerField(
        help_text="Population of the strata with at least one sampled unit."
    )
    unrepresented_strata = serializers.IntegerField()
    unrepresented_population = serializers.IntegerField()
    flagged_strata = serializers.IntegerField()
    integrity = serializers.ChoiceField(choices=("ok", "attention"))
    srm_alpha = serializers.FloatField()


class SamplingBalanceCategorySerializer(serializers.Serializer):
    value = serializers.CharField(allow_null=True)
    present = serializers.BooleanField()
    population_size = serializers.IntegerField()
    achieved = serializers.IntegerField()
    population_share = serializers.FloatField()
    sample_share = serializers.FloatField(allow_null=True)
    weighted_share = serializers.FloatField(allow_null=True)
    standardized_difference = serializers.FloatField(allow_null=True)
    weighted_standardized_difference = serializers.FloatField(allow_null=True)


class SamplingBalanceSerializer(serializers.Serializer):
    attribute = serializers.CharField()
    source = serializers.CharField(allow_null=True)
    categories = SamplingBalanceCategorySerializer(many=True)
    chi_square = serializers.FloatField(allow_null=True)
    degrees_of_freedom = serializers.IntegerField(allow_null=True)
    p_value = serializers.FloatField(allow_null=True)
    max_abs_standardized_difference = serializers.FloatField(allow_null=True)
    max_abs_weighted_standardized_difference = serializers.FloatField(allow_null=True)
    weighted_imbalance = serializers.BooleanField()


class SamplingAuditResultSerializer(serializers.Serializer):
    cohort_id = serializers.UUIDField()
    policy_version = serializers.CharField()
    mode = serializers.ChoiceField(choices=("closed", "live"))
    sampling_unit = serializers.CharField()
    coordination = serializers.ChoiceField(choices=("independent", "project"))
    frozen_at = serializers.DateTimeField(allow_null=True)
    complete = serializers.BooleanField()
    population_source = serializers.ChoiceField(
        choices=("frozen_snapshot", "persisted_decisions")
    )
    strata = SamplingAuditStratumSerializer(many=True)
    summary = SamplingAuditSummarySerializer()
    balance = SamplingBalanceSerializer(many=True)
    notes = serializers.ListField(child=serializers.CharField())


class SamplingAuditResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SamplingAuditResultSerializer()
