from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

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


class AnalyzeErrorsInput(PydanticBaseModel):
    project_id: UUID | None = Field(default=None, description="Filter by project ID")
    limit: int = Field(default=50, ge=1, le=200, description="Max traces to analyze")
    days: int = Field(default=7, ge=1, le=30, description="Look back N days")
    page_number: int = Field(default=0, ge=0, description="Page number (0-indexed)")
    page_size: int = Field(default=50, ge=1, le=200, description="Results per page")


@register_tool
class AnalyzeErrorsTool(BaseTool):
    name = "analyze_errors"
    description = (
        "Analyzes error patterns across traces and spans. Groups errors by type, "
        "shows frequency, affected spans, and error messages to help debug issues."
    )
    category = "tracing"
    input_model = AnalyzeErrorsInput

    def execute(self, params: AnalyzeErrorsInput, context: ToolContext) -> ToolResult:
        from collections import Counter
        from datetime import UTC, datetime, timedelta

        from django.db.models import Q

        from tracer.models.trace import Trace
        from tracer.services.clickhouse.v2 import get_reader

        since = datetime.now(UTC) - timedelta(days=params.days)

        from tracer.models.project import Project

        # Validate project belongs to user's organization if provided
        if params.project_id:
            try:
                Project.objects.get(
                    id=params.project_id,
                    organization=context.organization,
                )
            except Project.DoesNotExist:
                return ToolResult.not_found("Project", str(params.project_id))

        # Find traces with errors, scoped to user's organization
        trace_qs = Trace.objects.filter(
            created_at__gte=since,
            project__organization=context.organization,
        ).exclude(Q(error__isnull=True) | Q(error={}))
        if params.project_id:
            trace_qs = trace_qs.filter(project_id=params.project_id)

        trace_qs = trace_qs.select_related("project").order_by("-created_at")

        total_traces_period = Trace.objects.filter(
            created_at__gte=since,
            project__organization=context.organization,
        )
        if params.project_id:
            total_traces_period = total_traces_period.filter(
                project_id=params.project_id
            )
        total_count = total_traces_period.count()

        error_count = trace_qs.count()
        offset = params.page_number * params.page_size
        error_traces = trace_qs[offset : offset + params.page_size]

        # Find spans with errors — was ObservationSpan.objects.filter(
        # trace__in=error_traces, status="error", deleted=False)
        # .order_by("-created_at"). CH read fetches all spans for those
        # trace ids in one query; we filter status="error" in Python.
        # Ordering reverses the CH default (start_time, id) since the
        # consumer only needs the top 200 for pattern analysis below;
        # reverse-Python after fetch.
        error_trace_ids = [str(t.id) for t in error_traces]
        with get_reader() as reader:
            all_trace_spans = reader.list_by_trace_ids(error_trace_ids)
        error_spans = sorted(
            (s for s in all_trace_spans if (s.status or "").lower() == "error"),
            key=lambda s: s.start_time or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

        # Analyze error patterns
        error_messages = Counter()
        error_by_type = Counter()
        error_by_project = Counter()
        error_by_model = Counter()

        for span in error_spans[:200]:
            span_type = span.observation_type or "unknown"
            error_by_type[span_type] += 1

            if span.model:
                error_by_model[span.model] += 1

        for trace in error_traces:
            project_name = trace.project.name if trace.project else "Unknown"
            error_by_project[project_name] += 1

            error_obj = trace.error
            if isinstance(error_obj, dict):
                msg = error_obj.get("message", error_obj.get("error", str(error_obj)))
            else:
                msg = str(error_obj)
            # Normalize error messages by taking first 80 chars
            error_messages[truncate(msg, 80)] += 1

        # Build output
        error_rate = (error_count / total_count * 100) if total_count > 0 else 0

        info = key_value_block(
            [
                ("Period", f"Last {params.days} days"),
                ("Total Traces", str(total_count)),
                ("Error Traces", str(error_count)),
                ("Error Rate", f"{format_number(error_rate)}%"),
                ("Error Spans", str(len(error_spans))),
            ]
        )
        content = section("Error Analysis", info)

        # Top error messages
        if error_messages:
            content += "\n\n### Top Error Messages\n\n"
            rows = [[msg, str(count)] for msg, count in error_messages.most_common(10)]
            content += markdown_table(["Error Message", "Count"], rows)

        # Errors by span type
        if error_by_type:
            content += "\n\n### Errors by Span Type\n\n"
            rows = [
                [span_type, str(count)]
                for span_type, count in error_by_type.most_common(10)
            ]
            content += markdown_table(["Span Type", "Count"], rows)

        # Errors by project
        if error_by_project:
            content += "\n\n### Errors by Project\n\n"
            rows = [
                [project, str(count)]
                for project, count in error_by_project.most_common(10)
            ]
            content += markdown_table(["Project", "Count"], rows)

        # Errors by model
        if error_by_model:
            content += "\n\n### Errors by Model\n\n"
            rows = [
                [model, str(count)] for model, count in error_by_model.most_common(10)
            ]
            content += markdown_table(["Model", "Count"], rows)

        # Recent error traces
        content += "\n\n### Recent Error Traces\n\n"
        recent_rows = []
        for trace in error_traces[:10]:
            project_name = trace.project.name if trace.project else "—"
            error_obj = trace.error
            if isinstance(error_obj, dict):
                msg = error_obj.get("message", error_obj.get("error", str(error_obj)))
            else:
                msg = str(error_obj)

            recent_rows.append(
                [
                    f"`{str(trace.id)}`",
                    truncate(trace.name, 30) if trace.name else "—",
                    project_name,
                    truncate(msg, 50),
                    format_datetime(trace.created_at),
                ]
            )
        content += markdown_table(
            ["ID", "Name", "Project", "Error", "Created"], recent_rows
        )

        data = {
            "total_traces": total_count,
            "error_traces": error_count,
            "error_rate": error_rate,
            "top_errors": dict(error_messages.most_common(10)),
            "errors_by_type": dict(error_by_type),
            "errors_by_project": dict(error_by_project),
        }

        return ToolResult(content=content, data=data)
