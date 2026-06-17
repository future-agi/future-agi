"""Dashboard API endpoints for MCP usage analytics."""

from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncHour
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.models.session import MCPSession
from mcp_server.models.usage import MCPUsageRecord
from mcp_server.serializers.contracts import (
    MCPAnalyticsSummaryResponseSerializer,
    MCPAnalyticsTimelineResponseSerializer,
    MCPAnalyticsToolsResponseSerializer,
    MCPErrorResponseSerializer,
)
from tfc.utils.api_errors import build_error_envelope


class MCPAnalyticsSummaryView(APIView):
    """Usage summary (total calls, sessions, latency)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPAnalyticsSummaryResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timedelta(days=days)

        usage_qs = MCPUsageRecord.objects.filter(
            organization=organization,
            called_at__gte=since,
        )

        agg = usage_qs.aggregate(
            total_calls=Count("id"),
            avg_latency_ms=Avg("latency_ms"),
        )

        total_sessions = MCPSession.objects.filter(
            organization=organization,
            started_at__gte=since,
        ).count()

        active_sessions = MCPSession.objects.filter(
            organization=organization,
            status="active",
        ).count()

        error_count = usage_qs.filter(response_status="error").count()
        total = agg["total_calls"] or 0
        error_rate = (error_count / total * 100) if total > 0 else 0.0

        return Response(
            {
                "status": True,
                "result": {
                    "total_calls": total,
                    "total_sessions": total_sessions,
                    "avg_latency_ms": round(agg["avg_latency_ms"] or 0, 1),
                    "error_rate": round(error_rate, 2),
                    "active_sessions": active_sessions,
                },
            }
        )


class MCPAnalyticsToolsView(APIView):
    """Per-tool usage breakdown."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPAnalyticsToolsResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timedelta(days=days)

        breakdown = (
            MCPUsageRecord.objects.filter(
                organization=organization,
                called_at__gte=since,
            )
            .values("tool_name")
            .annotate(
                call_count=Count("id"),
                avg_latency_ms=Avg("latency_ms"),
                error_count=Count("id", filter=Q(response_status="error")),
            )
            .order_by("-call_count")
        )

        tools = []
        for row in breakdown:
            total = row["call_count"]
            tools.append(
                {
                    "tool_name": row["tool_name"],
                    "call_count": total,
                    "avg_latency_ms": round(row["avg_latency_ms"] or 0, 1),
                    "error_rate": round(
                        row["error_count"] / total * 100 if total else 0, 2
                    ),
                }
            )

        return Response({"status": True, "result": tools})


class MCPAnalyticsTimelineView(APIView):
    """Tool calls over time (hourly buckets)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPAnalyticsTimelineResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timedelta(days=days)

        timeline = (
            MCPUsageRecord.objects.filter(
                organization=organization,
                called_at__gte=since,
            )
            .annotate(hour=TruncHour("called_at"))
            .values("hour")
            .annotate(call_count=Count("id"))
            .order_by("hour")
        )

        return Response(
            {
                "status": True,
                "result": [
                    {"timestamp": row["hour"], "call_count": row["call_count"]}
                    for row in timeline
                ],
            }
        )
