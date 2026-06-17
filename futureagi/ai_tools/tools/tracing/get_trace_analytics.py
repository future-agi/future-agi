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
    "30d": 24 * 30,
}


class GetTraceAnalyticsInput(PydanticBaseModel):
    project_id: Optional[UUID] = Field(default=None, description="Filter by project ID")
    time_range: str = Field(
        default="24h",
        description="Time range to analyze: 1h, 6h, 24h, 7d, or 30d",
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
        from tracer.services.clickhouse.v2 import get_reader

        # Parse time range
        hours = TIME_RANGE_MAP.get(params.time_range)
        if hours is None:
            return ToolResult.error(
                f"Invalid time_range '{params.time_range}'. "
                f"Valid options: {', '.join(TIME_RANGE_MAP.keys())}",
                error_code="VALIDATION_ERROR",
            )

        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Trace stats (KEEP-PG: Trace model is not yet migrated to CH; its
        # `error` JSONField + `created_at` filter have no CH counterpart on
        # this branch).
        trace_qs = Trace.objects.filter(
            created_at__gte=since, project__organization=context.organization
        )
        if params.project_id:
            trace_qs = trace_qs.filter(project_id=params.project_id)

        total_traces = trace_qs.count()
        error_traces = trace_qs.exclude(Q(error__isnull=True) | Q(error={})).count()
        error_rate = (error_traces / total_traces * 100) if total_traces > 0 else 0

        # Span stats.
        #
        # PG fallback `span_qs` is always built — the group-by branches
        # below still route through PG (no CH reader covers per-model /
        # per-status group-by yet — see CH25-TODO at each branch).
        span_qs = ObservationSpan.objects.filter(
            trace__created_at__gte=since,
            deleted=False,
            project__organization=context.organization,
        )
        if params.project_id:
            span_qs = span_qs.filter(project_id=params.project_id)

        # Main span aggregate: route to CH when project_id is set.
        #
        # The legacy filter was `trace__created_at__gte=since` — a join on
        # the Trace FK to filter by trace.created_at. CH spans don't carry
        # trace.created_at, so we pre-fetch trace_ids from the already-
        # built (org+project) PG `trace_qs` and feed them to
        # `per_trace_aggregate`. This preserves the original semantic
        # (spans of traces created in the window) without silently
        # shifting to `start_time`.
        #
        # The pre-fetched trace_ids list is bounded by `total_traces` (the
        # PG count of matching traces). For very large org-wide queries
        # without a project_id this could exceed reasonable CH IN-clause
        # size, so we KEEP-PG for the no-project_id branch. A future
        # reader extension would close this gap:
        #
        #   aggregate_by_organization(org_id, *, since, until,
        #                             trace_ids=None) ->
        #     {span_count, prompt_tokens, completion_tokens,
        #      total_tokens, cost, avg_latency_ms}
        if params.project_id:
            trace_ids_str = [
                str(tid) for tid in trace_qs.values_list("id", flat=True)
            ]
            with get_reader() as reader:
                per_trace = reader.per_trace_aggregate(trace_ids_str)
            # Sum across all matching traces. `per_trace_aggregate`'s
            # `latency_ms` is `sum(latency_ms)` per trace, so total_lat =
            # sum-of-sums across traces; avg over spans is reconstructed
            # as total_lat / total_spans.
            #
            # PG-vs-CH semantic note: legacy `Avg("latency_ms")` on PG
            # excluded null-latency rows from the denominator. The CH
            # adapter (tracer/services/clickhouse/v2/adapter.py:376)
            # coerces null `latency_ms` to 0 at write time, so post-
            # cutover those rows count in the denominator. This matches
            # the system-wide `avg(latency_ms)` semantic used by every
            # other wave-2/wave-3 reader method (see span_reader.py:616,
            # 738, 851, 910, 1060, 1105). The drift is a property of the
            # CH-as-canonical-store choice, not a regression introduced
            # by this migration.
            span_count = sum(v.get("span_count", 0) for v in per_trace.values())
            total_tokens = sum(v.get("total_tokens", 0) for v in per_trace.values())
            total_prompt_tokens = sum(
                v.get("prompt_tokens", 0) for v in per_trace.values()
            )
            total_completion_tokens = sum(
                v.get("completion_tokens", 0) for v in per_trace.values()
            )
            total_cost = sum(v.get("cost", 0.0) for v in per_trace.values())
            total_lat_sum = sum(v.get("latency_ms", 0) for v in per_trace.values())
            avg_latency = (total_lat_sum / span_count) if span_count else 0
            agg = {
                "span_count": span_count,
                "total_tokens": total_tokens,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "total_cost": total_cost,
                "avg_latency": avg_latency,
            }
        else:
            # KEEP-PG (org-wide rollup): no project_id means an unbounded
            # IN clause; route through PG until the reader gains
            # `aggregate_by_organization(org_id, *, since, until)`.
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
            # CH25-TODO: no reader method covers group-by-model with these
            # aggregates yet. `per_project_group_by_name` groups by `name`
            # only; we'd need a generalised
            #   per_project_group_by(project_id, *, group_by_field,
            #                        observation_type=None, since=None,
            #                        until=None, status_filter=None,
            #                        limit=50) -> [{<group_value>,
            #                                       usage_count, tokens,
            #                                       cost, avg_latency_ms}]
            # to route the model/status/operation_name branches. KEEP-PG
            # until a reader extension lands.
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
            # CH25-TODO: same reader-extension gap as group-by-model above.
            # KEEP-PG until `per_project_group_by(group_by_field='status',
            # ...)` exists.
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
            # KEEP-PG: groups by `Trace.name`, not span name; Trace is not
            # migrated. `per_project_group_by_name` exists for spans but
            # would require switching to `span.name` which changes the
            # business meaning of this breakdown.
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
