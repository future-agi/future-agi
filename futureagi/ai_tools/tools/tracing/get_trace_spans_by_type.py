from django.core.exceptions import ValidationError
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text
from ai_tools.tools.tracing._error_utils import candidate_error_analysis_traces_result

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
    trace_id: str = Field(default="", description="Trace name or UUID")
    observation_type: str = Field(
        default="llm",
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
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        trace_ref = clean_ref(params.trace_id)
        if not trace_ref:
            return candidate_error_analysis_traces_result(
                context,
                "Trace Required",
                "Provide `trace_id` plus `observation_type` to list spans by type.",
            )

        trace = None
        ref_uuid = uuid_text(trace_ref)
        try:
            qs = Trace.objects.filter(project__organization=context.organization)
            if ref_uuid:
                trace = qs.get(id=ref_uuid)
            else:
                exact = qs.filter(name__iexact=trace_ref)
                if exact.count() == 1:
                    trace = exact.first()
                else:
                    fuzzy = qs.filter(name__icontains=trace_ref)
                    if fuzzy.count() == 1:
                        trace = fuzzy.first()
        except (Trace.DoesNotExist, ValidationError, ValueError, TypeError):
            trace = None
        if trace is None:
            return candidate_error_analysis_traces_result(
                context,
                "Trace Not Found",
                f"Trace `{trace_ref}` was not found. Use one of these IDs instead.",
            )

        obs_type = params.observation_type.lower().strip()
        if obs_type not in VALID_TYPES:
            return ToolResult.validation_error(
                f"Invalid observation_type '{obs_type}'. "
                f"Must be one of: {', '.join(VALID_TYPES)}"
            )

        spans = ObservationSpan.objects.filter(
            trace_id=trace.id,
            observation_type=obs_type,
            deleted=False,
        ).order_by("start_time", "created_at")[:50]

        if not spans:
            return ToolResult(
                content=section(
                    f"Spans (type={obs_type})",
                    f"No `{obs_type}` spans found in trace `{trace.id}`.",
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
