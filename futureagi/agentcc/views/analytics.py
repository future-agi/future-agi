import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from agentcc.models import AgentccRequestLog
from agentcc.serializers.analytics import (
    GatewayGuardrailRulesApiResponseSerializer,
    GatewayOverviewApiResponseSerializer,
)
from agentcc.serializers.request_log import AgentccRequestLogSerializer
from agentcc.serializers.request_queries import GatewayOverviewQuerySerializer
from agentcc.services.analytics import (
    get_cost_breakdown,
    get_error_breakdown,
    get_guardrail_overview,
    get_guardrail_rules,
    get_guardrail_trends,
    get_latency_stats,
    get_model_comparison,
    get_overview_kpis,
    get_usage_timeseries,
    parse_time_range,
    parse_time_zone,
)
from agentcc.views.request_log_scope import OrganizationRequestLogScopeMixin
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _parse_int(value, default):
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class AgentccAnalyticsViewSet(OrganizationRequestLogScopeMixin, GenericViewSet):
    """Analytics endpoints for the Agentcc gateway dashboard.

    All endpoints are read-only GET actions that aggregate data from
    AgentccRequestLog. They read the rows the Logs view authorizes
    (``OrganizationRequestLogScopeMixin``): an active Owner or Admin counts
    the organization's logs, which ingestion writes with no workspace,
    whichever workspace is selected; anyone else gets the shared mixin's
    organization and workspace scope.

    The time-bucketed endpoints (usage-timeseries, latency-stats,
    error-breakdown, guardrail-trends) bucket by ``granularity`` (5m, 15m, 1h,
    6h, 1d, minute, hour, day, week, month; default hour) and answer 400 for
    any other value. The others do not bucket and ignore it. They also take
    ``tz`` (an IANA zone): day, week and month buckets start at midnight
    there, and each series names the zone it used in ``time_zone`` (UTC when
    ``tz`` is missing or unknown, and for the fixed sizes 5m to 1d, which are
    cut from the Unix epoch). A window of more than 2,000 buckets is a 400.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccRequestLogSerializer  # for mixin model discovery
    queryset = AgentccRequestLog.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        queryset = self._get_authorized_queryset()
        api_key_id = self.request.query_params.get("api_key_id")
        if api_key_id:
            queryset = queryset.filter(api_key_id=api_key_id)
        return queryset

    @validated_request(
        query_serializer=GatewayOverviewQuerySerializer,
        responses={200: GatewayOverviewApiResponseSerializer},
    )
    @action(detail=False, methods=["get"], pagination_class=None)
    def overview(self, request):
        """KPI cards with trend comparison."""
        try:
            period_start, period_end, _ = parse_time_range(
                request.query_params, validate_granularity=False
            )
            result = get_overview_kpis(self.get_queryset(), period_start, period_end)
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_overview_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="usage-timeseries")
    def usage_timeseries(self, request):
        """Time-bucketed usage data for charts."""
        try:
            period_start, period_end, granularity = parse_time_range(
                request.query_params
            )
            group_by = request.query_params.get("group_by")
            result = get_usage_timeseries(
                self.get_queryset(),
                period_start,
                period_end,
                granularity,
                group_by,
                zone=parse_time_zone(request.query_params),
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_usage_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="cost-breakdown")
    def cost_breakdown(self, request):
        """Cost breakdown by dimension."""
        try:
            period_start, period_end, _ = parse_time_range(
                request.query_params, validate_granularity=False
            )
            group_by = request.query_params.get("group_by", "model")
            top_n = _parse_int(request.query_params.get("top_n"), 10)
            result = get_cost_breakdown(
                self.get_queryset(), period_start, period_end, group_by, top_n
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_cost_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="latency-stats")
    def latency_stats(self, request):
        """Latency percentiles and timeseries."""
        try:
            period_start, period_end, granularity = parse_time_range(
                request.query_params
            )
            group_by = request.query_params.get("group_by")
            result = get_latency_stats(
                self.get_queryset(),
                period_start,
                period_end,
                granularity,
                group_by,
                zone=parse_time_zone(request.query_params),
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_latency_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="error-breakdown")
    def error_breakdown(self, request):
        """Error analysis with breakdown and timeseries."""
        try:
            period_start, period_end, granularity = parse_time_range(
                request.query_params
            )
            group_by = request.query_params.get("group_by", "status_code")
            top_n = _parse_int(request.query_params.get("top_n"), 10)
            result = get_error_breakdown(
                self.get_queryset(),
                period_start,
                period_end,
                granularity,
                group_by,
                top_n,
                zone=parse_time_zone(request.query_params),
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_error_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="model-comparison")
    def model_comparison(self, request):
        """Side-by-side model performance comparison."""
        try:
            period_start, period_end, _ = parse_time_range(
                request.query_params, validate_granularity=False
            )
            models_param = request.query_params.get("models")
            models_list = None
            if models_param:
                models_list = [m.strip() for m in models_param.split(",") if m.strip()]

            result = get_model_comparison(
                self.get_queryset(),
                period_start,
                period_end,
                models_list,
                request.query_params.get("group_by", "model"),
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_model_comparison_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="guardrail-overview")
    def guardrail_overview(self, request):
        """Guardrail aggregate KPIs."""
        try:
            period_start, period_end, _ = parse_time_range(
                request.query_params, validate_granularity=False
            )
            result = get_guardrail_overview(
                self.get_queryset(), period_start, period_end
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_guardrail_overview_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(responses={200: GatewayGuardrailRulesApiResponseSerializer})
    @action(detail=False, methods=["get"], url_path="guardrail-rules")
    def guardrail_rules(self, request):
        """Per-rule guardrail trigger breakdown."""
        try:
            period_start, period_end, _ = parse_time_range(
                request.query_params, validate_granularity=False
            )
            result = get_guardrail_rules(self.get_queryset(), period_start, period_end)
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_guardrail_rules_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="guardrail-trends")
    def guardrail_trends(self, request):
        """Time-bucketed guardrail trigger trends."""
        try:
            period_start, period_end, granularity = parse_time_range(
                request.query_params
            )
            result = get_guardrail_trends(
                self.get_queryset(),
                period_start,
                period_end,
                granularity,
                zone=parse_time_zone(request.query_params),
            )
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("analytics_guardrail_trends_error", error=str(e))
            return self._gm.bad_request(str(e))
