"""The answer of a Console agent's learning curves (tracer/views/omega_agent_curves.py; the
figures and their rules are tracer/services/omega_agents/curves/aggregate.py)."""

from rest_framework import serializers

from tracer.services.omega_agents.curves import aggregate

_REASONS = [
    aggregate.NO_RUNS,
    aggregate.NO_PRICED_RUNS,
    aggregate.NO_MODEL_CALLS,
    aggregate.CHARGED_NOTHING,
    aggregate.NO_COMPLETED_RUNS,
    aggregate.UNDER_ONE_MILLISECOND,
    aggregate.TOO_FEW_RUNS,
    aggregate.NO_PREVIOUS_VERSION,
]


class OmegaAgentCurveMetricSerializer(serializers.Serializer):
    # USD for cost figures, milliseconds for latency, a 0..1 share for the success rate.
    value = serializers.FloatField(allow_null=True)
    # The runs the figure was computed over (for cost, the priced runs).
    n = serializers.IntegerField(min_value=0)
    # Why the value is missing or zero; null otherwise.
    reason = serializers.ChoiceField(choices=_REASONS, allow_null=True)


class OmegaAgentCurveSummarySerializer(serializers.Serializer):
    n = serializers.IntegerField(min_value=0)
    completed = serializers.IntegerField(min_value=0)
    failed = serializers.IntegerField(min_value=0)
    unfinished = serializers.IntegerField(min_value=0)
    pricedRuns = serializers.IntegerField(min_value=0)
    # Runs with an LLM span that carries no ledger cost: left out of every cost figure.
    unpricedRuns = serializers.IntegerField(min_value=0)
    # The time these runs waited on a person, taken out of their latency.
    approvalWaitExcludedMs = serializers.IntegerField(min_value=0)
    outcomeSuccessRate = OmegaAgentCurveMetricSerializer()
    totalCostUsd = OmegaAgentCurveMetricSerializer()
    costPerRunUsd = OmegaAgentCurveMetricSerializer()
    costPer1kRunsUsd = OmegaAgentCurveMetricSerializer()
    p50LatencyMs = OmegaAgentCurveMetricSerializer()
    p95LatencyMs = OmegaAgentCurveMetricSerializer()


class OmegaAgentCurvePointSerializer(OmegaAgentCurveSummarySerializer):
    day = serializers.DateField()
    version = serializers.IntegerField(allow_null=True)


class OmegaAgentCurveVersionSerializer(OmegaAgentCurveSummarySerializer):
    version = serializers.IntegerField(allow_null=True)
    firstDay = serializers.DateField()
    lastDay = serializers.DateField()


class OmegaAgentCurveTileSerializer(OmegaAgentCurveMetricSerializer):
    previousValue = serializers.FloatField(allow_null=True)
    previousN = serializers.IntegerField(min_value=0)
    # This version's value minus the previous version's; null with deltaReason when either
    # side has no value or too few runs.
    delta = serializers.FloatField(allow_null=True)
    deltaReason = serializers.ChoiceField(choices=_REASONS, allow_null=True)


class OmegaAgentCurveTilesSerializer(serializers.Serializer):
    costPer1kRunsUsd = OmegaAgentCurveTileSerializer()
    p50LatencyMs = OmegaAgentCurveTileSerializer()
    outcomeSuccessRate = OmegaAgentCurveTileSerializer()


class OmegaAgentCurveOverviewSerializer(serializers.Serializer):
    version = serializers.IntegerField(allow_null=True)
    previousVersion = serializers.IntegerField(allow_null=True)
    tiles = OmegaAgentCurveTilesSerializer()


class OmegaAgentCurveWindowSerializer(serializers.Serializer):
    startDate = serializers.DateField()
    endDate = serializers.DateField()
    timezone = serializers.ChoiceField(choices=["UTC"])


class OmegaAgentCurvesResultSerializer(serializers.Serializer):
    agentId = serializers.CharField(max_length=255)
    projectId = serializers.UUIDField()
    window = OmegaAgentCurveWindowSerializer()
    # Tiles and deltas need at least this many runs on each side.
    minimumN = serializers.IntegerField(min_value=1)
    totals = OmegaAgentCurveSummarySerializer()
    versions = OmegaAgentCurveVersionSerializer(many=True)
    points = OmegaAgentCurvePointSerializer(many=True)
    overview = OmegaAgentCurveOverviewSerializer()


class OmegaAgentCurvesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OmegaAgentCurvesResultSerializer()
