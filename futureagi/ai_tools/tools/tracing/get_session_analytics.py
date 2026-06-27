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


class GetSessionAnalyticsInput(PydanticBaseModel):
    session_id: UUID = Field(description="The UUID of the session to analyze")


@register_tool
class GetSessionAnalyticsTool(BaseTool):
    name = "get_session_analytics"
    description = (
        "Returns aggregated analytics for a trace session, including total tokens, "
        "total cost, error count, average latency, and per-model breakdown."
    )
    category = "tracing"
    input_model = GetSessionAnalyticsInput

    def execute(
        self, params: GetSessionAnalyticsInput, context: ToolContext
    ) -> ToolResult:
        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession
        from tracer.services.clickhouse.v2 import get_reader

        try:
            session = TraceSession.objects.select_related("project").get(
                id=params.session_id, project__organization=context.organization
            )
        except TraceSession.DoesNotExist:
            return ToolResult.not_found("Session", str(params.session_id))

        # Trace-level stats (still PG; Trace model not migrated yet)
        traces = Trace.objects.filter(session=session)
        trace_count = traces.count()
        error_traces = traces.exclude(error__isnull=True).exclude(error={}).count()

        # Span-level aggregations — read from CH 25.3. Was:
        #   spans = ObservationSpan.objects.filter(trace__session=session,
        #                                          deleted=False)
        #   spans.aggregate(Sum/Avg/Count over multiple fields)
        # CHSpanReader's session_aggregate returns the same shape via a
        # single SQL aggregation. Per-model and per-type breakdowns below
        # compute in Python from the loaded span list (sessions are
        # bounded; load cost is acceptable).
        with get_reader() as reader:
            spans_list = reader.list_by_session(str(session.id))

        span_count = len(spans_list)
        total_prompt_tokens = sum(s.prompt_tokens or 0 for s in spans_list)
        total_completion_tokens = sum(s.completion_tokens or 0 for s in spans_list)
        total_tokens = sum(s.total_tokens or 0 for s in spans_list)
        total_cost = sum(s.cost or 0.0 for s in spans_list)
        latencies = [s.latency_ms for s in spans_list if s.latency_ms]
        avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
        agg = {
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_cost": total_cost,
            "avg_latency": avg_latency,
            "span_count": span_count,
        }
        error_spans = sum(1 for s in spans_list if (s.status or "").upper() == "ERROR")

        info = key_value_block(
            [
                ("Session ID", f"`{params.session_id}`"),
                ("Session Name", session.name or "—"),
                ("Project", session.project.name if session.project else "—"),
                ("Total Traces", str(trace_count)),
                ("Error Traces", str(error_traces)),
                (
                    "Error Rate",
                    f"{format_number(error_traces / trace_count * 100 if trace_count else 0)}%",
                ),
                ("Total Spans", str(agg["span_count"] or 0)),
                ("Error Spans", str(error_spans)),
                ("Total Tokens", str(agg["total_tokens"] or 0)),
                ("Prompt Tokens", str(agg["total_prompt_tokens"] or 0)),
                ("Completion Tokens", str(agg["total_completion_tokens"] or 0)),
                ("Total Cost", f"${format_number(agg['total_cost'] or 0, 4)}"),
                ("Avg Latency", f"{format_number(agg['avg_latency'] or 0)}ms"),
            ]
        )

        content = section(f"Session Analytics: {session.name or str(session.id)}", info)

        # Per-model breakdown — group the loaded span list in Python.
        # Equivalent to:
        #   spans.exclude(model__isnull=True).exclude(model="")
        #        .values("model").annotate(count, tokens, cost, avg_lat)
        #        .order_by("-count")[:15]
        from collections import defaultdict as _dd
        _by_model: dict[str, dict[str, float]] = _dd(
            lambda: {"count": 0, "tokens": 0, "cost": 0.0, "_lat_sum": 0.0, "_lat_n": 0}
        )
        for s in spans_list:
            if not s.model:
                continue
            m = _by_model[s.model]
            m["count"] += 1
            m["tokens"] += s.total_tokens or 0
            m["cost"] += s.cost or 0.0
            if s.latency_ms:
                m["_lat_sum"] += s.latency_ms
                m["_lat_n"] += 1
        model_stats = [
            {
                "model": model,
                "count": int(m["count"]),
                "tokens": int(m["tokens"]),
                "cost": float(m["cost"]),
                "avg_lat": (m["_lat_sum"] / m["_lat_n"]) if m["_lat_n"] else 0.0,
            }
            for model, m in sorted(_by_model.items(), key=lambda kv: -kv[1]["count"])[:15]
        ]

        if model_stats:
            content += "\n\n### Per-Model Breakdown\n\n"
            model_rows = []
            for ms in model_stats:
                model_rows.append(
                    [
                        ms["model"],
                        str(ms["count"]),
                        str(ms["tokens"] or 0),
                        f"${format_number(ms['cost'] or 0, 4)}",
                        f"{format_number(ms['avg_lat'] or 0)}ms",
                    ]
                )
            content += markdown_table(
                ["Model", "Spans", "Tokens", "Cost", "Avg Latency"], model_rows
            )

        # Per-type breakdown — same in-Python pattern.
        _by_type: dict[str, dict[str, float]] = _dd(
            lambda: {"count": 0, "tokens": 0, "cost": 0.0}
        )
        for s in spans_list:
            t = s.observation_type or ""
            _by_type[t]["count"] += 1
            _by_type[t]["tokens"] += s.total_tokens or 0
            _by_type[t]["cost"] += s.cost or 0.0
        type_stats = [
            {
                "observation_type": t or None,
                "count": int(d["count"]),
                "tokens": int(d["tokens"]),
                "cost": float(d["cost"]),
            }
            for t, d in sorted(_by_type.items(), key=lambda kv: -kv[1]["count"])
        ]

        if type_stats:
            content += "\n\n### Per-Type Breakdown\n\n"
            type_rows = []
            for ts in type_stats:
                type_rows.append(
                    [
                        ts["observation_type"] or "—",
                        str(ts["count"]),
                        str(ts["tokens"] or 0),
                        f"${format_number(ts['cost'] or 0, 4)}",
                    ]
                )
            content += markdown_table(["Type", "Spans", "Tokens", "Cost"], type_rows)

        data = {
            "session_id": str(params.session_id),
            "trace_count": trace_count,
            "error_traces": error_traces,
            "total_tokens": agg["total_tokens"] or 0,
            "total_cost": float(agg["total_cost"] or 0),
            "avg_latency_ms": float(agg["avg_latency"] or 0),
            "span_count": agg["span_count"] or 0,
        }

        return ToolResult(content=content, data=data)
