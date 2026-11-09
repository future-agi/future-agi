"""Response contracts of the gateway analytics actions.

``AgentccAnalyticsViewSet`` (agentcc/views/analytics.py) answers each action
with ``GeneralMethods.success_response(result)``: ``{status, result}`` with
HTTP 200, where ``result`` is what the analytics service returns.
"""

from rest_framework import serializers

TREND_HELP_TEXT = (
    "Percent change from the previous period of the same length; null when "
    "the previous period was zero and this one is not."
)


class GatewayOverviewIntegerKpiSerializer(serializers.Serializer):
    value = serializers.IntegerField()
    trend = serializers.FloatField(allow_null=True, help_text=TREND_HELP_TEXT)


class GatewayOverviewNumberKpiSerializer(serializers.Serializer):
    value = serializers.FloatField()
    trend = serializers.FloatField(allow_null=True, help_text=TREND_HELP_TEXT)


class GatewayOverviewCostKpiSerializer(serializers.Serializer):
    value = serializers.CharField(help_text="Total cost as a decimal string.")
    trend = serializers.FloatField(allow_null=True, help_text=TREND_HELP_TEXT)
    unpriced_requests = serializers.IntegerField(
        help_text="Requests in the period that have no price and add no cost."
    )


class GatewayOverviewResultSerializer(serializers.Serializer):
    """``get_overview_kpis()`` (agentcc/services/analytics.py)."""

    total_requests = GatewayOverviewIntegerKpiSerializer()
    total_tokens = GatewayOverviewIntegerKpiSerializer()
    total_cost = GatewayOverviewCostKpiSerializer()
    avg_latency_ms = GatewayOverviewNumberKpiSerializer()
    error_rate = GatewayOverviewNumberKpiSerializer()
    cache_hit_rate = GatewayOverviewNumberKpiSerializer()
    p95_latency_ms = GatewayOverviewNumberKpiSerializer()
    active_models = GatewayOverviewIntegerKpiSerializer()
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()


class GatewayOverviewApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = GatewayOverviewResultSerializer()

    class Meta:
        ref_name = None


class GatewayGuardrailRuleSerializer(serializers.Serializer):
    rule = serializers.CharField()
    trigger_count = serializers.IntegerField()
    block_count = serializers.IntegerField()
    warn_count = serializers.IntegerField()
    avg_latency_ms = serializers.FloatField()


class GatewayGuardrailRulesResultSerializer(serializers.Serializer):
    """``get_guardrail_rules()`` (agentcc/services/analytics.py)."""

    rules = GatewayGuardrailRuleSerializer(many=True)


class GatewayGuardrailRulesApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = GatewayGuardrailRulesResultSerializer()

    class Meta:
        ref_name = None
