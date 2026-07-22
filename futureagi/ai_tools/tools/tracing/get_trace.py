from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetTraceInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace to retrieve")
    include_spans: bool = Field(default=True, description="Include span details")


@register_tool
class GetTraceTool(BaseTool):
    name = "get_trace"
    description = (
        "Returns detailed information about a specific trace, including "
        "its spans (LLM calls, tool calls, etc.), timing, tokens, and costs."
    )
    category = "tracing"
    input_model = GetTraceInput

    def execute(self, params: GetTraceInput, context: ToolContext) -> ToolResult:

        from tracer.models.trace import Trace
        from tracer.services.clickhouse.v2 import get_reader

        try:
            trace = Trace.objects.select_related("project").get(
                id=params.trace_id, project__organization=context.organization
            )
        except Trace.DoesNotExist:
            return ToolResult.not_found("Trace", str(params.trace_id))

        project_name = trace.project.name if trace.project else "—"
        has_error = bool(trace.error and trace.error != {})

        info = key_value_block(
            [
                ("ID", f"`{trace.id}`"),
                ("Name", trace.name or "—"),
                ("Project", project_name),
                ("Error", "Yes" if has_error else "No"),
                ("Tags", ", ".join(trace.tags) if trace.tags else "—"),
                ("External ID", trace.external_id or "—"),
                ("Created", format_datetime(trace.created_at)),
                (
                    "Link",
                    dashboard_link("trace", str(trace.id), label="View in Dashboard"),
                ),
            ]
        )
        content = section(f"Trace: {trace.name or str(trace.id)}", info)

        # Show input/output if available
        if trace.input:
            content += (
                f"\n\n### Input\n\n```json\n{truncate(str(trace.input), 500)}\n```"
            )
        if trace.output:
            content += (
                f"\n\n### Output\n\n```json\n{truncate(str(trace.output), 500)}\n```"
            )
        if has_error:
            content += (
                f"\n\n### Error\n\n```json\n{truncate(str(trace.error), 500)}\n```"
            )

        # Spans (read from CH 25.3 — was ObservationSpan.objects.filter;
        # see DECISIONS D-027 + in-flight ORM migration). CHSpanReader
        # filters is_deleted=0 and orders by start_time, id — matches the
        # PG path's deleted=False + start_time ordering. Secondary sort
        # was created_at in PG; CH uses id which is functionally equivalent
        # for tie-breaking display order.
        span_data = []
        if params.include_spans:
            with get_reader() as reader:
                spans = reader.list_by_trace(str(trace.id))

            if spans:
                # Summary stats
                total_tokens = sum(s.total_tokens or 0 for s in spans)
                total_cost = sum(s.cost or 0 for s in spans)
                span_count = len(spans)

                content += f"\n\n### Spans ({span_count})\n\n"
                content += key_value_block(
                    [
                        ("Total Spans", str(span_count)),
                        ("Total Tokens", str(total_tokens) if total_tokens else "—"),
                        (
                            "Total Cost",
                            f"${format_number(total_cost, 4)}" if total_cost else "—",
                        ),
                    ]
                )

                # Span table
                span_rows = []
                for span in spans[:30]:  # Limit to 30 spans
                    duration = f"{span.latency_ms}ms" if span.latency_ms else "—"
                    tokens = str(span.total_tokens) if span.total_tokens else "—"
                    cost = f"${format_number(span.cost, 4)}" if span.cost else "—"

                    span_rows.append(
                        [
                            truncate(span.name, 30),
                            span.observation_type or "—",
                            span.model or "—",
                            duration,
                            tokens,
                            cost,
                            span.status or "—",
                        ]
                    )
                    span_data.append(
                        {
                            "id": span.id,
                            "name": span.name,
                            "type": span.observation_type,
                            "model": span.model,
                            "latency_ms": span.latency_ms,
                            "total_tokens": span.total_tokens,
                            "cost": float(span.cost) if span.cost else None,
                            "status": span.status,
                        }
                    )

                span_table = markdown_table(
                    ["Name", "Type", "Model", "Duration", "Tokens", "Cost", "Status"],
                    span_rows,
                )
                content += f"\n\n{span_table}"

                if len(spans) > 30:
                    content += f"\n\n_Showing 30 of {len(spans)} spans._"

        data = {
            "id": str(trace.id),
            "name": trace.name,
            "project": project_name,
            "has_error": has_error,
            "tags": trace.tags,
            "spans": span_data,
        }

        return ToolResult(content=content, data=data)
