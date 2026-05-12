from typing import Optional

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
from ai_tools.tools.tracing._error_utils import candidate_error_clusters_result


class GetErrorClusterDetailInput(PydanticBaseModel):
    cluster_id: str = Field(
        default="",
        description="The cluster ID to look up (e.g., 'KA1B2C3D4' or 'C01')",
    )
    include_traces: bool = Field(
        default=True,
        description="Include affected trace IDs with their error details",
    )
    trace_limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of traces to include in the response",
    )


IMPACT_DISPLAY = {
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
    "MINIMAL": "Minimal",
}


@register_tool
class GetErrorClusterDetailTool(BaseTool):
    name = "get_error_cluster_detail"
    description = (
        "Returns detailed information about a specific error cluster including "
        "error type, description, impact, affected traces with their individual "
        "error details, trend data, and first/last seen timestamps. Use after "
        "list_error_clusters to drill into a specific error pattern."
    )
    category = "error_feed"
    input_model = GetErrorClusterDetailInput

    def execute(
        self, params: GetErrorClusterDetailInput, context: ToolContext
    ) -> ToolResult:
        from tracer.queries.error_analysis import TraceErrorAnalysisDB
        from tracer.views.error_analysis import parse_error_type_and_name

        db = TraceErrorAnalysisDB()

        if not params.cluster_id:
            return candidate_error_clusters_result(
                context,
                "Error Cluster Required",
                "Choose one of these cluster IDs for details.",
                limit=params.trace_limit,
            )

        # Look up cluster with access check
        cluster = db.get_cluster_with_access_check(
            params.cluster_id, str(context.organization_id)
        )
        if not cluster:
            return candidate_error_clusters_result(
                context,
                "Error Cluster Not Found",
                f"Cluster `{params.cluster_id}` was not found or is not accessible.",
                search=params.cluster_id,
                limit=params.trace_limit,
            )

        category, error_name = parse_error_type_and_name(cluster.error_type)
        impact = cluster.combined_impact or "MEDIUM"

        # Build cluster summary
        info = key_value_block(
            [
                ("Cluster ID", f"`{cluster.cluster_id}`"),
                ("Error", error_name),
                ("Category", category),
                ("Impact", IMPACT_DISPLAY.get(impact, impact)),
                ("Total Events", str(cluster.total_events or 0)),
                ("Unique Traces", str(cluster.unique_traces or 0)),
                ("Unique Users", str(cluster.unique_users or 0)),
                ("First Seen", format_datetime(cluster.first_seen)),
                ("Last Seen", format_datetime(cluster.last_seen)),
                ("Project", cluster.project.name if cluster.project else "—"),
            ]
        )

        content = section("Error Cluster Detail", info)

        # Description
        if cluster.combined_description:
            content += (
                f"\n\n### Description\n\n{truncate(cluster.combined_description, 800)}"
            )

        # Cluster data for structured output
        cluster_data = {
            "cluster_id": cluster.cluster_id,
            "error_name": error_name,
            "error_category": category,
            "impact": impact,
            "total_events": cluster.total_events or 0,
            "unique_traces": cluster.unique_traces or 0,
            "unique_users": cluster.unique_users or 0,
            "first_seen": (
                cluster.first_seen.isoformat() if cluster.first_seen else None
            ),
            "last_seen": cluster.last_seen.isoformat() if cluster.last_seen else None,
            "project_id": str(cluster.project_id),
        }

        # Optionally include trace-level details
        traces_data = []
        if params.include_traces:
            trace_ids = db.get_cluster_trace_ids(
                cluster.cluster_id, limit=params.trace_limit
            )

            if trace_ids:
                # Get error details for these traces
                error_details = db.get_error_details_for_traces(trace_ids)

                # Group details by trace
                details_by_trace: dict[str, list] = {}
                for detail in error_details:
                    tid = str(detail.analysis.trace_id)
                    if tid not in details_by_trace:
                        details_by_trace[tid] = []
                    details_by_trace[tid].append(detail)

                # Get analysis summaries
                analyses = db.get_analyses_for_traces(trace_ids)
                analysis_by_trace = {str(a.trace_id): a for a in analyses}

                # Build trace summary table
                trace_rows = []
                for tid in trace_ids:
                    tid_str = str(tid)
                    analysis = analysis_by_trace.get(tid_str)
                    details = details_by_trace.get(tid_str, [])

                    score = (
                        format_number(analysis.overall_score)
                        if analysis and analysis.overall_score is not None
                        else "—"
                    )
                    trace_link = dashboard_link(
                        "trace",
                        tid_str,
                        str(context.workspace_id) if context.workspace_id else None,
                        label=tid_str[:8],
                    )

                    trace_rows.append(
                        [
                            trace_link,
                            score,
                            str(len(details)),
                            (analysis.recommended_priority if analysis else "—"),
                            (
                                format_datetime(analysis.analysis_date)
                                if analysis
                                else "—"
                            ),
                        ]
                    )

                    # Collect per-trace data
                    trace_entry = {
                        "trace_id": tid_str,
                        "score": (
                            float(analysis.overall_score)
                            if analysis and analysis.overall_score is not None
                            else None
                        ),
                        "error_count": len(details),
                        "errors": [],
                    }
                    for d in details[:10]:
                        trace_entry["errors"].append(
                            {
                                "error_id": d.error_id,
                                "category": d.category,
                                "impact": d.impact,
                                "description": (
                                    truncate(d.description, 200)
                                    if d.description
                                    else None
                                ),
                                "root_causes": d.root_causes or [],
                                "recommendation": (
                                    truncate(d.recommendation, 200)
                                    if d.recommendation
                                    else None
                                ),
                            }
                        )
                    traces_data.append(trace_entry)

                content += "\n\n### Affected Traces\n\n"
                content += markdown_table(
                    ["Trace", "Score", "Errors", "Priority", "Analyzed"],
                    trace_rows,
                )

                # Show representative error details from the first few traces
                shown_details = 0
                max_details = 8
                for tid_str, details in details_by_trace.items():
                    if shown_details >= max_details:
                        break
                    for d in details:
                        if shown_details >= max_details:
                            break
                        content += f"\n\n**{d.error_id}** ({d.impact}) — {truncate(d.category, 80)}\n"
                        if d.description:
                            content += f"> {truncate(d.description, 300)}\n"
                        if d.root_causes:
                            causes = ", ".join(
                                truncate(str(rc), 100) for rc in d.root_causes[:3]
                            )
                            content += f"Root causes: {causes}\n"
                        if d.recommendation:
                            content += (
                                f"Recommendation: {truncate(d.recommendation, 200)}\n"
                            )
                        shown_details += 1

        cluster_data["traces"] = traces_data

        return ToolResult(content=content, data=cluster_data)
