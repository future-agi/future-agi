from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool

VALID_TYPES = [
    "llm",
    "tool",
    "guardrail",
    "retriever",
    "agent",
    "chain",
    "embedding",
    "reranker",
    "evaluator",
    "conversation",
    "unknown",
]


class GetTraceSpansByTypeInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace")
    observation_type: str = Field(
        description=(
            "Span type to filter by: llm, tool, guardrail, retriever, "
            "agent, chain, embedding, reranker, evaluator, conversation, unknown"
        ),
    )


@register_tool
class GetTraceSpansByTypeTool(BaseTool):
    name = "get_trace_spans_by_type"
    description = (
        "Returns all spans of a specific type (e.g., 'llm', 'tool', 'retriever') "
        "in a trace. Useful for quickly finding all LLM calls, tool executions, "
        "or retrieval operations."
    )
    category = "tracing"
    input_model = GetTraceSpansByTypeInput

    def execute(
        self, params: GetTraceSpansByTypeInput, context: ToolContext
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

        obs_type = params.observation_type.lower().strip()
        if obs_type not in VALID_TYPES:
            return ToolResult.validation_error(
                f"Invalid observation_type '{obs_type}'. "
                f"Must be one of: {', '.join(VALID_TYPES)}"
            )

        # CH read replaces ObservationSpan.objects.filter(trace_id=,
        # observation_type=, deleted=False).order_by("start_time", "created_at")
        # [:50]. We load all spans for the trace (typically bounded) and
        # filter by observation_type in Python, then slice. Adding a typed
        # column filter to list_by_trace would be cheaper at very large
        # span-per-trace counts; defer until a real perf gap surfaces.
        with get_reader() as reader:
            all_spans = reader.list_by_trace(str(params.trace_id))
        spans = [s for s in all_spans if s.observation_type == obs_type][:50]

        if not spans:
            return ToolResult(
                content=section(
                    f"Spans (type={obs_type})",
                    f"No `{obs_type}` spans found in trace `{params.trace_id}`.",
                ),
                data={"spans": [], "count": 0},
            )

        rows = []
        data_list = []
        for s in spans:
            rows.append(
                [
                    f"`{s.id}`",
                    s.name or "—",
                    s.status or "—",
                    f"{s.latency_ms}ms" if s.latency_ms else "—",
                    s.model or "—",
                    str(s.total_tokens) if s.total_tokens else "—",
                ]
            )
            data_list.append(
                {
                    "span_id": str(s.id),
                    "name": s.name,
                    "status": s.status,
                    "latency_ms": s.latency_ms,
                    "model": s.model,
                    "total_tokens": s.total_tokens,
                }
            )

        content = section(
            f"{obs_type} Spans ({len(rows)})",
            markdown_table(
                ["ID", "Name", "Status", "Latency", "Model", "Tokens"],
                rows,
            ),
        )

        return ToolResult(
            content=content, data={"spans": data_list, "count": len(data_list)}
        )
