from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section


IMPACT_DISPLAY = {
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
    "MINIMAL": "Minimal",
}


def candidate_error_clusters_result(
    context: ToolContext,
    title: str = "Error Cluster Required",
    detail: str = "",
    days: int = 7,
    limit: int = 10,
    search: str | None = None,
) -> ToolResult:
    from tracer.queries.error_analysis import TraceErrorAnalysisDB
    from tracer.views.error_analysis import parse_error_type_and_name

    db = TraceErrorAnalysisDB()
    project_ids = db.get_user_accessible_projects(
        str(context.organization_id),
        str(context.workspace_id) if context.workspace_id else None,
    )
    if not project_ids:
        return ToolResult(
            content=section(
                title,
                detail or "No accessible projects found in this workspace.",
            ),
            data={"requires_cluster_id": True, "clusters": []},
        )

    result = db.get_clusters_for_feed(
        project_ids=project_ids,
        days=days,
        limit=limit,
        offset=0,
    )
    clusters = result.get("clusters", [])
    if search:
        search_lower = search.lower()
        clusters = [
            cluster
            for cluster in clusters
            if search_lower in (cluster.get("error_type") or "").lower()
            or search_lower in (cluster.get("cluster_id") or "").lower()
        ]

    rows = []
    cluster_data = []
    for cluster in clusters[:limit]:
        category, error_name = parse_error_type_and_name(
            cluster.get("error_type", "")
        )
        impact = cluster.get("combined_impact", "MEDIUM")
        rows.append(
            [
                f"`{cluster.get('cluster_id', '-')}`",
                error_name or category,
                IMPACT_DISPLAY.get(impact, impact),
                str(cluster.get("total_events", 0)),
                format_datetime(cluster.get("last_seen")),
                cluster.get("project_name", "-"),
            ]
        )
        cluster_data.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "error_name": error_name,
                "error_category": category,
                "impact": impact,
                "events": cluster.get("total_events", 0),
                "project_name": cluster.get("project_name"),
            }
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Cluster ID", "Error", "Impact", "Events", "Last Seen", "Project"],
            rows,
        )
    else:
        body = body or f"No error clusters found in the last {days} day(s)."

    return ToolResult(
        content=section(title, body),
        data={"requires_cluster_id": True, "clusters": cluster_data},
    )


def candidate_error_analysis_traces_result(
    context: ToolContext,
    title: str = "Trace Required",
    detail: str = "",
    limit: int = 10,
) -> ToolResult:
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
    qs = qs.exclude(error__isnull=True).exclude(error={})
    traces = list(qs[:limit])
    rows = [
        [
            trace.name or "-",
            f"`{trace.id}`",
            trace.project.name if trace.project else "-",
            format_datetime(trace.created_at),
        ]
        for trace in traces
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Trace ID", "Project", "Created"],
            rows,
        )
    else:
        body = body or "No recent traces with errors found."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_trace_id": True,
            "traces": [
                {
                    "id": str(trace.id),
                    "name": trace.name,
                    "project": trace.project.name if trace.project else None,
                }
                for trace in traces
            ],
        },
    )
