from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool


class SearchTraceSpansInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace to search in")
    keyword: str = Field(
        min_length=1,
        description="Keyword to search for across span input, output, and metadata (case-insensitive)",
    )


@register_tool
class SearchTraceSpansTool(BaseTool):
    name = "search_trace_spans"
    description = (
        "Searches across all span content (input, output, metadata) in a trace "
        "for a keyword. Returns matching spans and where the keyword was found. "
        "Use this to quickly find relevant spans instead of reading all of them."
    )
    category = "tracing"
    input_model = SearchTraceSpansInput

    def execute(
        self, params: SearchTraceSpansInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.clickhouse.v2 import get_reader
        from tracer.models.trace import Trace

        try:
            Trace.objects.get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except Trace.DoesNotExist:
            return ToolResult.not_found("Trace", str(params.trace_id))

        # CH read replaces ObservationSpan.objects.filter(trace_id=,
        # deleted=False). list_by_trace already filters is_deleted=0 and
        # orders by start_time, id.
        with get_reader() as reader:
            spans = reader.list_by_trace(str(params.trace_id))

        keyword_lower = params.keyword.lower()
        matches = []

        for span in spans:
            match_in = []
            if span.input and keyword_lower in str(span.input).lower():
                match_in.append("input")
            if span.output and keyword_lower in str(span.output).lower():
                match_in.append("output")
            if span.metadata and keyword_lower in str(span.metadata).lower():
                match_in.append("metadata")
            if span.span_events and keyword_lower in str(span.span_events).lower():
                match_in.append("events")

            if match_in:
                matches.append(
                    {
                        "span_id": str(span.id),
                        "name": span.name,
                        "type": span.observation_type,
                        "status": span.status,
                        "match_in": match_in,
                    }
                )

        if not matches:
            return ToolResult(
                content=section(
                    "Search Results",
                    f'No spans contain "{params.keyword}" in trace `{params.trace_id}`.',
                ),
                data={"matches": [], "count": 0},
            )

        rows = [
            [
                f"`{m['span_id']}`",
                m["name"] or "—",
                m["type"] or "—",
                m["status"] or "—",
                ", ".join(m["match_in"]),
            ]
            for m in matches[:30]
        ]

        content = section(
            f'Search: "{params.keyword}" ({len(matches)} match(es))',
            markdown_table(
                ["ID", "Name", "Type", "Status", "Found In"],
                rows,
            ),
        )

        return ToolResult(
            content=content, data={"matches": matches[:30], "count": len(matches)}
        )
