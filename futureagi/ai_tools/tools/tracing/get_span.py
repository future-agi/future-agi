import json
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field


def _tags_display(raw: str | None) -> str:
    """CHSpan.tags is a JSON string (could be a list, dict, or empty);
    render to a comma-separated string for the key-value display row."""
    if not raw:
        return "—"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if isinstance(parsed, list):
        return ", ".join(str(t) for t in parsed) if parsed else "—"
    if isinstance(parsed, dict):
        return ", ".join(f"{k}={v}" for k, v in parsed.items()) if parsed else "—"
    return str(parsed) if parsed else "—"

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetSpanInput(PydanticBaseModel):
    span_id: str = Field(description="The ID of the span/observation to retrieve")


@register_tool
class GetSpanTool(BaseTool):
    name = "get_span"
    description = (
        "Returns detailed information about a specific span/observation, including "
        "name, type, timing, model info, token counts, cost, input/output, "
        "parent span info, and child spans."
    )
    category = "tracing"
    input_model = GetSpanInput

    def execute(self, params: GetSpanInput, context: ToolContext) -> ToolResult:

        from tracer.models.project import Project
        from tracer.services.clickhouse.v2 import get_reader

        # Fetch span from CH 25.3 — was ObservationSpan.objects.select_related(
        # "trace", "project").get(id=, deleted=False,
        # project__organization=context.organization). The org-tenant check
        # is now a 2-step: CH lookup → PG Project lookup with org filter.
        # Two reads but each is fast (CH id-equality + PG id+org index).
        with get_reader() as reader:
            span = reader.get(params.span_id)
        if span is None:
            return ToolResult.not_found("Span", params.span_id)
        # Org/tenant scope check
        project = Project.objects.filter(
            id=span.project_id, organization=context.organization
        ).first()
        if project is None:
            return ToolResult.not_found("Span", params.span_id)

        # Calculate duration
        duration = f"{span.latency_ms}ms" if span.latency_ms else "—"

        info = key_value_block(
            [
                ("ID", f"`{span.id}`"),
                ("Name", span.name or "—"),
                ("Type", span.observation_type or "—"),
                ("Status", span.status or "—"),
                ("Model", span.model or "—"),
                ("Provider", span.provider or "—"),
                ("Duration", duration),
                ("Start Time", format_datetime(span.start_time)),
                ("End Time", format_datetime(span.end_time)),
                (
                    "Prompt Tokens",
                    str(span.prompt_tokens) if span.prompt_tokens else "—",
                ),
                (
                    "Completion Tokens",
                    str(span.completion_tokens) if span.completion_tokens else "—",
                ),
                ("Total Tokens", str(span.total_tokens) if span.total_tokens else "—"),
                ("Cost", f"${format_number(span.cost, 4)}" if span.cost else "—"),
                ("Trace", f"`{span.trace_id}`" if span.trace_id else "—"),
                ("Project", project.name if project else "—"),
                (
                    "Parent Span",
                    (
                        f"`{span.parent_span_id}`"
                        if span.parent_span_id
                        else "—(root span)"
                    ),
                ),
                # CHSpan.tags is a JSON string; parse for display.
                ("Tags", _tags_display(span.tags)),
                # CH spans table doesn't carry `created_at`; start_time is the
                # closest equivalent for the "when was this observed" view.
                ("Created", format_datetime(span.start_time)),
            ]
        )

        content = section(f"Span: {span.name or span.id}", info)

        # Input/Output
        if span.input:
            content += (
                f"\n\n### Input\n\n```json\n{truncate(str(span.input), 500)}\n```"
            )
        if span.output:
            content += (
                f"\n\n### Output\n\n```json\n{truncate(str(span.output), 500)}\n```"
            )

        # Model parameters — CH spans don't carry this column yet; future
        # schema work may add it. For now the section is omitted.

        # Metadata
        if span.metadata:
            content += (
                f"\n\n### Metadata\n\n```json\n{truncate(str(span.metadata), 300)}\n```"
            )

        # Child spans — was ObservationSpan.objects.filter(parent_span_id=,
        # deleted=False).order_by("start_time", "created_at")[:20]. CH's
        # list_by_parent already filters is_deleted=0 + orders by start_time,
        # id; explicit limit=20 caps the result.
        with get_reader() as reader:
            children = reader.list_by_parent(str(span.id), limit=20)

        if children:
            content += f"\n\n### Child Spans ({len(children)})\n\n"
            child_rows = []
            for child in children:
                child_dur = f"{child.latency_ms}ms" if child.latency_ms else "—"
                child_rows.append(
                    [
                        f"`{str(child.id)[:12]}...`",
                        truncate(child.name, 30),
                        child.observation_type or "—",
                        child.model or "—",
                        child_dur,
                        child.status or "—",
                    ]
                )
            content += markdown_table(
                ["ID", "Name", "Type", "Model", "Duration", "Status"],
                child_rows,
            )

        data = {
            "id": str(span.id),
            "name": span.name,
            "type": span.observation_type,
            "model": span.model,
            "status": span.status,
            "latency_ms": span.latency_ms,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "cost": float(span.cost) if span.cost else None,
            "trace_id": str(span.trace_id) if span.trace_id else None,
            "parent_span_id": span.parent_span_id,
        }

        return ToolResult(content=content, data=data)
