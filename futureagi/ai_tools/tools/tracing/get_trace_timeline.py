from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool

TIME_RANGE_MAP = {
    "1h": 1,
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
}

INTERVAL_MAP = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "6h": 360,
    "12h": 720,
    "24h": 1440,
}


class GetTraceTimelineInput(PydanticBaseModel):
    project_id: Optional[UUID] = Field(default=None, description="Filter by project ID")
    time_range: str = Field(
        default="24h",
        description="Time range: 1h, 6h, 12h, 24h, 7d, or 30d",
    )
    interval: str = Field(
        default="1h",
        description="Time bucket interval: 5m, 15m, 30m, 1h, 6h, 12h, or 24h",
    )


@register_tool
class GetTraceTimelineTool(BaseTool):
    name = "get_trace_timeline"
    description = (
        "Returns a time-bucketed trace activity timeline showing trace counts, "
        "error counts, and total tokens per time interval."
    )
    category = "tracing"
    input_model = GetTraceTimelineInput

    def execute(
        self, params: GetTraceTimelineInput, context: ToolContext
    ) -> ToolResult:
        from datetime import datetime, timedelta, timezone

        from django.db.models import Count, Q

        from tracer.services.clickhouse.v2 import get_reader
        from tracer.models.trace import Trace

        # Parse time range
        range_hours = TIME_RANGE_MAP.get(params.time_range)
        if range_hours is None:
            return ToolResult.error(
                f"Invalid time_range '{params.time_range}'. "
                f"Valid options: {', '.join(TIME_RANGE_MAP.keys())}",
                error_code="VALIDATION_ERROR",
            )

        interval_minutes = INTERVAL_MAP.get(params.interval)
        if interval_minutes is None:
            return ToolResult.error(
                f"Invalid interval '{params.interval}'. "
                f"Valid options: {', '.join(INTERVAL_MAP.keys())}",
                error_code="VALIDATION_ERROR",
            )

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=range_hours)
        interval_delta = timedelta(minutes=interval_minutes)

        # Generate time buckets
        buckets = []
        current = since
        while current < now:
            bucket_end = min(current + interval_delta, now)
            buckets.append((current, bucket_end))
            current = bucket_end

        # Build base queryset
        trace_qs = Trace.objects.filter(
            created_at__gte=since, project__organization=context.organization
        )
        if params.project_id:
            trace_qs = trace_qs.filter(project_id=params.project_id)

        # Query per bucket
        rows = []
        data_list = []
        total_traces = 0
        total_errors = 0

        for bucket_start, bucket_end in buckets:
            bucket_traces = trace_qs.filter(
                created_at__gte=bucket_start,
                created_at__lt=bucket_end,
            )

            count = bucket_traces.count()
            error_count = bucket_traces.exclude(
                Q(error__isnull=True) | Q(error={})
            ).count()

            # Get token count for this bucket's spans from CH 25.3 — was
            # ObservationSpan.objects.filter(trace__in=, deleted=False)
            # .aggregate(tokens=Sum("total_tokens")). The reader's
            # aggregate_by_trace_ids returns the same sum across all input
            # trace_ids in one CH query.
            bucket_trace_ids = list(bucket_traces.values_list("id", flat=True))
            with get_reader() as reader:
                span_agg = reader.aggregate_by_trace_ids(
                    [str(t) for t in bucket_trace_ids]
                )
            tokens = span_agg.get("total_tokens", 0) or 0
            total_traces += count
            total_errors += error_count

            time_label = bucket_start.strftime("%Y-%m-%d %H:%M")

            rows.append(
                [
                    time_label,
                    str(count),
                    str(error_count),
                    str(tokens),
                ]
            )
            data_list.append(
                {
                    "time": time_label,
                    "timestamp": bucket_start.isoformat(),
                    "traces": count,
                    "errors": error_count,
                    "tokens": tokens,
                }
            )

        table = markdown_table(["Time", "Traces", "Errors", "Tokens"], rows)

        error_rate = (total_errors / total_traces * 100) if total_traces > 0 else 0

        header = (
            f"**Time Range:** {params.time_range} | "
            f"**Interval:** {params.interval} | "
            f"**Buckets:** {len(buckets)}\n"
            f"**Total Traces:** {total_traces} | "
            f"**Total Errors:** {total_errors} | "
            f"**Error Rate:** {format_number(error_rate)}%"
        )

        content = section("Trace Timeline", f"{header}\n\n{table}")

        return ToolResult(
            content=content,
            data={
                "timeline": data_list,
                "total_traces": total_traces,
                "total_errors": total_errors,
                "time_range": params.time_range,
                "interval": params.interval,
            },
        )
