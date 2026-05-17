"""
ClickHouse Health Check View

Exposes a JSON endpoint for monitoring ClickHouse connectivity, CDC replication
lag, and per-query-type routing configuration.
"""

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from tracer.serializers.health import ClickHouseHealthResponseSerializer
from tracer.services.clickhouse.consistency import ConsistencyChecker

logger = structlog.get_logger(__name__)


class ClickHouseHealthView(APIView):
    """
    Health check endpoint for the ClickHouse analytics backend.

    Returns JSON with:
    - status: healthy | degraded | unhealthy | disabled
    - clickhouse_connected: bool
    - cdc_lag: per-table replication lag in seconds
    - routing: per-query-type routing configuration

    No authentication required (intended for infrastructure monitoring).
    """

    authentication_classes = []
    permission_classes = []

    @swagger_auto_schema(
        responses={
            200: ClickHouseHealthResponseSerializer,
            503: ClickHouseHealthResponseSerializer,
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            checker = ConsistencyChecker()
            health = checker.get_health_status()

            return Response(
                {
                    "status": health.status,
                    "clickhouse_connected": health.clickhouse_connected,
                    "cdc_lag": health.cdc_lag,
                    "routing": health.details.get("routing", {}),
                },
                status=200,
            )
        except Exception as e:
            logger.error("clickhouse_health_check_failed", error=str(e))
            return Response(
                {
                    "status": "unhealthy",
                    "clickhouse_connected": False,
                    "cdc_lag": {},
                    "routing": {},
                    "error": str(e),
                },
                status=503,
            )
