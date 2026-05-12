from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool

TIME_RANGE_MAP = {
    "1h": 1,
    "6h": 6,
    "24h": 24,
    "7d": 24 * 7,
    "14d": 24 * 14,
    "30d": 24 * 30,
}


class GetTraceAnalyticsInput(PydanticBaseModel):
    project_id: Optional[UUID] = Field(default=None, description="Filter by project ID")
    time_range: str = Field(
        default="24h",
        description="Time range to analyze: 1h, 6h, 24h, 7d, 14d, or 30d",
    )
    group_by: Optional[str] = Field(
        default=None,
        description="Group results by: status, model, or name",
    )


@register_tool
class GetTraceAnalyticsTool(BaseTool):
    name = "get_trace_analytics"
    description = (
        "Returns aggregated analytics for traces, including trace count, error rate, "
        "average latency, token usage, and cost. Optionally grouped by status, model, or name."
    )
    category = "tracing"
    input_model = GetTraceAnalyticsInput

    def execute(
        self, params: GetTraceAnalyticsInput, context: ToolContext
    ) -> ToolResult:
        from datetime import datetime, timedelta, timezone

        from django.db.models import Avg, Count, Q, Sum

        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        # Parse time range
        hours = TIME_RANGE_MAP.get(params.time_range)
        if hours is None:
            return ToolResult.error(
                f"Invalid time_range '{params.time_range}'. "
                f"Valid options: {', '.join(TIME_RANGE_MAP.keys())}",
                error_code="VALIDATION_ERROR",
            )

        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Trace stats
        trace_qs = Trace.objects.filter(
            created_at__gte=since, project__organization=context.organization
        )
        if params.project_id:
            trace_qs = trace_qs.filter(project_id=params.project_id)

        total_traces = trace_qs.count()
        error_traces = trace_qs.exclude(Q(error__isnull=True) | Q(error={})).count()
        error_rate = (error_traces / total_traces * 100) if total_traces > 0 else 0

        # Span stats
        span_qs = ObservationSpan.objects.filter(
            trace__created_at__gte=since,
            deleted=False,
            project__organization=context.organization,
        )
        if params.project_id:
            span_qs = span_qs.filter(project_id=params.project_id)

        agg = span_qs.aggregate(
            total_tokens=Sum("total_tokens"),
            total_prompt_tokens=Sum("prompt_tokens"),
            total_completion_tokens=Sum("completion_tokens"),
            total_cost=Sum("cost"),
            avg_latency=Avg("latency_ms"),
            span_count=Count("id"),
        )

        info = key_value_block(
            [
                ("Time Range", params.time_range),
                (
                    "Project",
                    f"`{params.project_id}`" if params.project_id else "All projects",
                ),
                ("Total Traces", str(total_traces)),
                ("Error Traces", str(error_traces)),
                ("Error Rate", f"{format_number(error_rate)}%"),
                ("Total Spans", str(agg["span_count"] or 0)),
                ("Total Tokens", str(agg["total_tokens"] or 0)),
                ("Prompt Tokens", str(agg["total_prompt_tokens"] or 0)),
                ("Completion Tokens", str(agg["total_completion_tokens"] or 0)),
                ("Total Cost", f"${format_number(agg['total_cost'] or 0, 4)}"),
                ("Avg Latency", f"{format_number(agg['avg_latency'] or 0)}ms"),
            ]
        )

        content = section("Trace Analytics", info)

        # Group-by breakdown
        if params.group_by == "model":
            model_stats = (
                span_qs.exclude(model__isnull=True)
                .exclude(model="")
                .values("model")
                .annotate(
                    count=Count("id"),
                    tokens=Sum("total_tokens"),
                    cost=Sum("cost"),
                    avg_lat=Avg("latency_ms"),
                )
                .order_by("-count")[:20]
            )
            if model_stats:
                content += "\n\n### By Model\n\n"
                rows = []
                for ms in model_stats:
                    rows.append(
                        [
                            ms["model"],
                            str(ms["count"]),
                            str(ms["tokens"] or 0),
                            f"${format_number(ms['cost'] or 0, 4)}",
                            f"{format_number(ms['avg_lat'] or 0)}ms",
                        ]
                    )
                content += markdown_table(
                    ["Model", "Spans", "Tokens", "Cost", "Avg Latency"], rows
                )

        elif params.group_by == "status":
            status_stats = (
                span_qs.values("status")
                .annotate(
                    count=Count("id"),
                    tokens=Sum("total_tokens"),
                    cost=Sum("cost"),
                )
                .order_by("-count")
            )
            if status_stats:
                content += "\n\n### By Status\n\n"
                rows = []
                for ss in status_stats:
                    rows.append(
                        [
                            ss["status"] or "—",
                            str(ss["count"]),
                            str(ss["tokens"] or 0),
                            f"${format_number(ss['cost'] or 0, 4)}",
                        ]
                    )
                content += markdown_table(["Status", "Spans", "Tokens", "Cost"], rows)

        elif params.group_by == "name":
            name_stats = (
                trace_qs.values("name")
                .annotate(count=Count("id"))
                .order_by("-count")[:20]
            )
            if name_stats:
                content += "\n\n### By Trace Name\n\n"
                rows = []
                for ns in name_stats:
                    rows.append([ns["name"] or "—", str(ns["count"])])
                content += markdown_table(["Name", "Count"], rows)

        data = {
            "time_range": params.time_range,
            "total_traces": total_traces,
            "error_traces": error_traces,
            "error_rate": error_rate,
            "total_tokens": agg["total_tokens"] or 0,
            "total_cost": float(agg["total_cost"] or 0),
            "avg_latency_ms": float(agg["avg_latency"] or 0),
            "span_count": agg["span_count"] or 0,
        }

        return ToolResult(content=content, data=data)
