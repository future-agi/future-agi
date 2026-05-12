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


class ListProjectsInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    trace_type: str | None = Field(
        default=None,
        description="Filter by project type: 'experiment' or 'observe'",
    )
    search: str | None = Field(
        default=None,
        description="Search projects by name (case-insensitive)",
    )


@register_tool
class ListProjectsTool(BaseTool):
    name = "list_projects"
    description = (
        "Lists tracing projects in the current workspace. "
        "Projects are containers for traces and observations. "
        "Returns project name, type, trace count, and creation time."
    )
    category = "tracing"
    input_model = ListProjectsInput

    def execute(self, params: ListProjectsInput, context: ToolContext) -> ToolResult:
        from django.db.models import Count, Q
        from tracer.models.project import Project

        qs = (
            Project.objects.filter(
                organization=context.organization,
            )
            .annotate(trace_count=Count("traces", filter=Q(traces__deleted=False)))
            .order_by("-created_at")
        )

        if context.workspace:
            qs = qs.filter(Q(workspace=context.workspace) | Q(workspace__isnull=True))

        if params.trace_type:
            qs = qs.filter(trace_type=params.trace_type)
        if params.search:
            qs = qs.filter(name__icontains=params.search)

        total = qs.count()
        projects = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for p in projects:
            rows.append(
                [
                    dashboard_link(
                        "project", str(p.id), label=f"{truncate(p.name, 40)} (`{p.id}`)"
                    ),
                    p.trace_type or "—",
                    str(p.trace_count),
                    p.source or "—",
                    format_datetime(p.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(p.id),
                    "name": p.name,
                    "trace_type": p.trace_type,
                    "trace_count": p.trace_count,
                    "source": p.source,
                }
            )

        table = markdown_table(
            ["Name (ID)", "Type", "Traces", "Source", "Created"], rows
        )

        showing = f"Showing {len(rows)} of {total}"
        if params.trace_type:
            showing += f" (type: {params.trace_type})"

        content = section(f"Projects ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"projects": data_list, "total": total})
