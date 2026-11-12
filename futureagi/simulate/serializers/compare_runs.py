"""The N-run compare endpoint's query and response.

Kept out of the serializers package's eager imports: the response reuses the v3
calls page's serializers, whose module imports that package.
"""

from __future__ import annotations

from uuid import UUID

from rest_framework import serializers

from simulate.services.run_call_match import MATCH_FILTERS
from simulate.services.run_results_v3_queries import GROUP_FIELDS
from simulate.views.run_results_v3 import (
    EvaluationColumnSerializer,
    LevelLabelsField,
    OutcomeCountsSerializer,
    PersonaDetailsSerializer,
    RunCallFiltersSerializer,
    RunCallSerializer,
    RunCallsSummarySerializer,
    RunExecutionSerializer,
    RunFacetsSerializer,
    RunGroupSerializer,
)

MIN_COMPARE_RUNS, MAX_COMPARE_RUNS = 2, 8


class HarnessCompareRunsQuerySerializer(RunCallFiltersSerializer):
    """The runs to compare and the run detail's search and filters, by scenario row."""

    runs = serializers.CharField(
        help_text="2-8 comma-separated run ids; the first is the baseline."
    )
    group_by = serializers.ChoiceField(
        choices=list(GROUP_FIELDS), allow_blank=True, required=False, default=""
    )
    ordering = serializers.ChoiceField(
        choices=["movement", "scenario"], required=False, default="movement"
    )
    page = serializers.IntegerField(default=1, min_value=1, required=False)
    page_size = serializers.IntegerField(
        default=25, min_value=1, max_value=100, required=False
    )

    def validate_runs(self, value: str) -> list[UUID]:
        try:
            runs = [UUID(one.strip()) for one in value.split(",") if one.strip()]
        except ValueError as exc:
            raise serializers.ValidationError("Runs must be run ids.") from exc
        if not MIN_COMPARE_RUNS <= len(runs) <= MAX_COMPARE_RUNS:
            raise serializers.ValidationError(
                f"Pick {MIN_COMPARE_RUNS} to {MAX_COMPARE_RUNS} runs."
            )
        if len(set(runs)) != len(runs):
            raise serializers.ValidationError("Each run can be picked once.")
        return runs

    def validate_filters(self, value):
        value = super().validate_filters(value)
        unsupported = set(value) - set(MATCH_FILTERS)
        if unsupported:
            raise serializers.ValidationError(
                f"Unsupported filters: {', '.join(sorted(unsupported))}."
            )
        return value


class HarnessCompareRunSerializer(serializers.Serializer):
    execution = RunExecutionSerializer()
    is_baseline = serializers.BooleanField()
    # The run's calls that match the search and filters.
    summary = RunCallsSummarySerializer()
    status_counts = OutcomeCountsSerializer()
    histograms = serializers.DictField(
        child=serializers.ListField(child=serializers.IntegerField()),
        help_text="Per evaluation, matching calls' scores in ten 10% bands.",
    )


class HarnessCompareGroupSerializer(serializers.Serializer):
    """A group of scenario rows; ``row_ids`` are the ones on this page."""

    key = serializers.CharField()
    label = serializers.CharField()
    row_ids = serializers.ListField(child=serializers.CharField())
    row_count = serializers.IntegerField(
        help_text="The scenario rows in this group on every page."
    )
    runs = serializers.DictField(
        child=RunGroupSerializer(),
        help_text="By run id: that run's calls page group for the same filters.",
    )


class HarnessCompareRowSerializer(serializers.Serializer):
    row_id = serializers.CharField()
    scenario_key = serializers.CharField()
    scenario = serializers.CharField()
    use_case = serializers.CharField()
    persona = serializers.CharField(allow_null=True)
    persona_details = PersonaDetailsSerializer(allow_null=True)
    situation = serializers.CharField(allow_null=True)
    ideal_outcome = serializers.CharField(allow_null=True)
    calls = serializers.DictField(
        child=RunCallSerializer(many=True),
        help_text="By run id, in trial order; empty when the run did not run it.",
    )
    movement = serializers.ChoiceField(
        choices=["regressed", "improved", "mixed", "same"]
    )


class HarnessCompareRunsSerializer(serializers.Serializer):
    """Runs of one environment, scenario version by scenario version."""

    baseline_execution_id = serializers.UUIDField()
    runs = HarnessCompareRunSerializer(many=True)
    evaluation_columns = EvaluationColumnSerializer(many=True)
    facets = RunFacetsSerializer()
    level_labels = LevelLabelsField()
    changed_count = serializers.IntegerField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField(
        help_text="Rows a page holds at most: fewer when its rows hold many calls."
    )
    total_pages = serializers.IntegerField()
    groups = HarnessCompareGroupSerializer(many=True)
    rows = HarnessCompareRowSerializer(many=True)
