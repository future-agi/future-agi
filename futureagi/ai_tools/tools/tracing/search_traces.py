from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class SearchTracesInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    project_id: UUID | None = Field(default=None, description="Filter by project ID")
    name: str | None = Field(
        default=None, description="Filter by trace name (case-insensitive contains)"
    )
    has_error: bool | None = Field(
        default=None, description="Filter to traces with/without errors"
    )
    tags: list[str] | None = Field(
        default=None,
        description="Filter by tags (traces must contain all specified tags)",
    )


@register_tool
class SearchTracesTool(BaseTool):
    name = "search_traces"
    description = (
        "Searches traces in the current workspace with optional filters. "
        "Returns trace name, project, error status, tags, and creation time."
    )
    category = "tracing"
    input_model = SearchTracesInput

    def execute(self, params: SearchTracesInput, context: ToolContext) -> ToolResult:

        from django.db.models import Q
        from tracer.models.trace import Trace

        qs = (
            Trace.objects.select_related("project")
            .filter(project__organization=context.organization)
            .order_by("-created_at")
        )

        if context.workspace:
            qs = qs.filter(
                Q(project__workspace=context.workspace)
                | Q(project__workspace__isnull=True)
            )

        if params.project_id:
            qs = qs.filter(project_id=params.project_id)
        if params.name:
            qs = qs.filter(name__icontains=params.name)
        if params.has_error is True:
            qs = qs.exclude(error__isnull=True).exclude(error={})
        elif params.has_error is False:
            qs = qs.filter(Q(error__isnull=True) | Q(error={}))
        if params.tags:
            for tag in params.tags:
                qs = qs.filter(tags__contains=[tag])

        total = qs.count()
        traces = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for trace in traces:
            project_name = trace.project.name if trace.project else "—"
            has_err = "Yes" if trace.error and trace.error != {} else "No"
            tag_str = ", ".join(trace.tags[:3]) if trace.tags else "—"
            if trace.tags and len(trace.tags) > 3:
                tag_str += f" (+{len(trace.tags) - 3})"

            trace_label = truncate(trace.name, 40) if trace.name else "—"
            rows.append(
                [
                    dashboard_link(
                        "trace", str(trace.id), label=f"{trace_label} (`{trace.id}`)"
                    ),
                    project_name,
                    has_err,
                    tag_str,
                    format_datetime(trace.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(trace.id),
                    "name": trace.name,
                    "project": project_name,
                    "project_id": str(trace.project_id) if trace.project_id else None,
                    "has_error": has_err == "Yes",
                    "tags": trace.tags,
                }
            )

        table = markdown_table(
            ["Name (ID)", "Project", "Error", "Tags", "Created"], rows
        )

        filters_desc = []
        if params.project_id:
            filters_desc.append(f"project={params.project_id}")
        if params.name:
            filters_desc.append(f"name contains '{params.name}'")
        if params.has_error is not None:
            filters_desc.append(f"has_error={params.has_error}")
        if params.tags:
            filters_desc.append(f"tags={params.tags}")

        showing = f"Showing {len(rows)} of {total}"
        if filters_desc:
            showing += f" (filters: {', '.join(filters_desc)})"

        content = section(f"Traces ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"traces": data_list, "total": total})
