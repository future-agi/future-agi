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
