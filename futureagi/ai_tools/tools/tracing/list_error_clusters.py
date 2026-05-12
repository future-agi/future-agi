from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class ListErrorClustersInput(PydanticBaseModel):
    days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Number of days to look back for errors",
    )
    project_id: Optional[UUID] = Field(
        default=None,
        description="Filter by a specific project UUID",
    )
    search: Optional[str] = Field(
        default=None,
        description="Search error names (case-insensitive substring match)",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of clusters to return",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Offset for pagination (0-indexed)",
    )


IMPACT_DISPLAY = {
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
    "MINIMAL": "Minimal",
}


@register_tool
class ListErrorClustersTool(BaseTool):
    name = "list_error_clusters"
    description = (
        "Lists error clusters from the feed, showing top recurring errors "
        "across traces. Returns cluster ID, error type, event count, affected "
        "users, impact level, and last seen time. Use to answer questions like "
        "'what are the most common errors?' or 'show me critical issues'."
    )
    category = "error_feed"
    input_model = ListErrorClustersInput

    def execute(
        self, params: ListErrorClustersInput, context: ToolContext
    ) -> ToolResult:
        from tracer.queries.error_analysis import TraceErrorAnalysisDB
        from tracer.views.error_analysis import parse_error_type_and_name

        db = TraceErrorAnalysisDB()

        # Get accessible projects
        accessible_projects = db.get_user_accessible_projects(
            str(context.organization_id),
            str(context.workspace_id) if context.workspace_id else None,
        )

        if not accessible_projects:
            return ToolResult(
                content=section(
                    "Error Clusters",
                    "No accessible projects found in this workspace.",
                ),
                data={"clusters": [], "total_count": 0},
            )

        # Filter to specific project if requested
        if params.project_id:
            pid = str(params.project_id)
            if pid not in [str(p) for p in accessible_projects]:
                return ToolResult.permission_denied(
                    f"Access denied to project `{pid}`."
                )
            project_ids = [pid]
        else:
            project_ids = accessible_projects

        # Fetch clusters
        result = db.get_clusters_for_feed(
            project_ids=project_ids,
            days=params.days,
            limit=params.limit,
            offset=params.offset,
        )

        clusters = result.get("clusters", [])
        total_count = result.get("total_count", 0)

        # Apply search filter (the SQL layer may not support it)
        if params.search:
            search_lower = params.search.lower()
            clusters = [
                c
                for c in clusters
                if search_lower in (c.get("error_type") or "").lower()
            ]
            total_count = len(clusters)

        if not clusters:
            return ToolResult(
                content=section(
                    "Error Clusters",
                    f"No error clusters found in the last {params.days} day(s).",
                ),
                data={"clusters": [], "total_count": 0},
            )

        # Build table
        rows = []
        cluster_data = []
        for c in clusters:
            category, error_name = parse_error_type_and_name(c.get("error_type", ""))
            impact = c.get("combined_impact", "MEDIUM")

            rows.append(
                [
                    c.get("cluster_id", "—"),
                    error_name or category,
                    IMPACT_DISPLAY.get(impact, impact),
                    c.get("total_events", 0),
                    c.get("unique_users", 0),
                    format_datetime(c.get("last_seen")),
                    c.get("project_name", "—"),
                ]
            )
            cluster_data.append(
                {
                    "cluster_id": c.get("cluster_id"),
                    "error_name": error_name,
                    "error_category": category,
                    "impact": impact,
                    "events": c.get("total_events", 0),
                    "users": c.get("unique_users", 0),
                    "project_name": c.get("project_name"),
                }
            )

        content = section(
            f"Error Clusters ({total_count} total)",
            markdown_table(
                [
                    "Cluster ID",
                    "Error",
                    "Impact",
                    "Events",
                    "Users",
                    "Last Seen",
                    "Project",
                ],
                rows,
            ),
        )

        # Pagination hint
        shown = params.offset + len(clusters)
        if shown < total_count:
            content += (
                f"\n\n_Showing {params.offset + 1}–{shown} of {total_count}. "
                f"Use offset={shown} to see more._"
            )

        return ToolResult(
            content=content,
            data={
                "clusters": cluster_data,
                "total_count": total_count,
                "days": params.days,
            },
        )
