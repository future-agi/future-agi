from collections import Counter
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


class AnalyzeErrorClusterInput(PydanticBaseModel):
    cluster_id: str = Field(
        default="",
        description="The cluster ID to analyze",
    )
    question: Optional[str] = Field(
        default=None,
        description=(
            "A specific question to investigate about this cluster, e.g., "
            "'why do these errors only happen for certain users?' or "
            "'what is the common root cause?'. If provided, the analysis "
            "data will include a context header to help you focus."
        ),
    )
    max_traces: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of traces to include in the analysis",
    )


IMPACT_DISPLAY = {
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
    "MINIMAL": "Minimal",
}


def _rank_items(items: list, limit: int = 10) -> list[tuple[str, int]]:
    """Count and rank items by frequency, returning (item, count) tuples."""
    counter: Counter = Counter()
    for item in items:
        if item:
            key = str(item).strip()
            if key:
                counter[key] += 1
    return counter.most_common(limit)


@register_tool
class AnalyzeErrorClusterTool(BaseTool):
    name = "analyze_error_cluster"
    description = (
        "Performs deep cross-trace analysis on an error cluster. Gathers "
        "error details from multiple traces, identifies common root causes, "
        "patterns, and provides aggregated data for actionable recommendations. "
        "More thorough than get_error_cluster_detail — use when the user wants "
        "to understand WHY errors are happening across traces."
    )
    category = "error_feed"
    input_model = AnalyzeErrorClusterInput

    def execute(
        self, params: AnalyzeErrorClusterInput, context: ToolContext
    ) -> ToolResult:
        from tracer.queries.error_analysis import TraceErrorAnalysisDB
        from tracer.views.error_analysis import parse_error_type_and_name

        db = TraceErrorAnalysisDB()

        if not params.cluster_id:
            return candidate_error_clusters_result(
                context,
                "Error Cluster Required For Analysis",
                "Choose one of these cluster IDs to analyze.",
                limit=params.max_traces,
            )

        # ── Cluster lookup with access check ────────────────────────
        cluster = db.get_cluster_with_access_check(
            params.cluster_id, str(context.organization_id)
        )
        if not cluster:
            return candidate_error_clusters_result(
                context,
                "Error Cluster Not Found",
                f"Cluster `{params.cluster_id}` was not found or is not accessible.",
                search=params.cluster_id,
                limit=params.max_traces,
            )

        category, error_name = parse_error_type_and_name(cluster.error_type)
        impact = cluster.combined_impact or "MEDIUM"

        # ── Gather trace data ───────────────────────────────────────
        trace_ids = db.get_cluster_trace_ids(
            cluster.cluster_id, limit=params.max_traces
        )

        if not trace_ids:
            info = key_value_block(
                [
                    ("Cluster ID", f"`{cluster.cluster_id}`"),
                    ("Error", error_name),
                    ("Impact", IMPACT_DISPLAY.get(impact, impact)),
                ]
            )
            return ToolResult(
                content=section(
                    "Cluster Analysis",
                    f"{info}\n\nNo traces found in this cluster for analysis.",
                ),
                data={"cluster_id": cluster.cluster_id, "traces_analyzed": 0},
            )

        error_details = list(db.get_error_details_for_traces(trace_ids))
        analyses = list(db.get_analyses_for_traces(trace_ids))
        analysis_by_trace = {str(a.trace_id): a for a in analyses}

        # ── Aggregate root causes ───────────────────────────────────
        all_root_causes: list[str] = []
        all_recommendations: list[str] = []
        all_evidence: list[tuple[str, str]] = []  # (snippet, trace_id)
        impact_counts: Counter = Counter()

        details_by_trace: dict[str, list] = {}
        for d in error_details:
            tid = str(d.analysis.trace_id)
            if tid not in details_by_trace:
                details_by_trace[tid] = []
            details_by_trace[tid].append(d)

            # Collect aggregation data
            for rc in d.root_causes or []:
                if rc:
                    all_root_causes.append(str(rc))
            if d.recommendation:
                all_recommendations.append(d.recommendation)
            for snippet in (d.evidence_snippets or [])[:2]:
                if snippet:
                    all_evidence.append((str(snippet), tid))
            if d.impact:
                impact_counts[d.impact] += 1

        # ── Score distribution ──────────────────────────────────────
        scores = [
            float(a.overall_score) for a in analyses if a.overall_score is not None
        ]
        fg_scores = [
            float(a.factual_grounding_score)
            for a in analyses
            if a.factual_grounding_score is not None
        ]
        ps_scores = [
            float(a.privacy_and_safety_score)
            for a in analyses
            if a.privacy_and_safety_score is not None
        ]
        ia_scores = [
            float(a.instruction_adherence_score)
            for a in analyses
            if a.instruction_adherence_score is not None
        ]
        ope_scores = [
            float(a.optimal_plan_execution_score)
            for a in analyses
            if a.optimal_plan_execution_score is not None
        ]

        def _avg(vals: list[float]) -> float | None:
            return sum(vals) / len(vals) if vals else None

        # ── Temporal pattern ────────────────────────────────────────
        trace_timestamps = []
        for a in analyses:
            if a.analysis_date:
                trace_timestamps.append(a.analysis_date)

        weekday_counts: Counter = Counter()
        hour_counts: Counter = Counter()
        for ts in trace_timestamps:
            weekday_counts[ts.strftime("%A")] += 1
            hour_counts[ts.hour] += 1

        # ── Build output ────────────────────────────────────────────
        content_parts: list[str] = []

        # Question context (if provided)
        if params.question:
            content_parts.append(f"**Investigation question:** {params.question}\n")

        # Overview section
        score_range = ""
        if scores:
            score_range = (
                f"{format_number(min(scores))} – {format_number(max(scores))} "
                f"(avg: {format_number(_avg(scores))})"
            )

        overview = key_value_block(
            [
                ("Cluster ID", f"`{cluster.cluster_id}`"),
                ("Error", error_name),
                ("Category", category),
                ("Impact", IMPACT_DISPLAY.get(impact, impact)),
                ("Traces Analyzed", str(len(trace_ids))),
                ("Total Errors", str(len(error_details))),
                ("Score Range", score_range or "—"),
                (
                    "Active Period",
                    (
                        f"{format_datetime(cluster.first_seen)} → {format_datetime(cluster.last_seen)}"
                    ),
                ),
                ("Unique Users", str(cluster.unique_users or 0)),
            ]
        )
        content_parts.append(section("Cluster Analysis", overview))

        # Root causes
        ranked_causes = _rank_items(all_root_causes, limit=8)
        if ranked_causes:
            lines = []
            for cause, count in ranked_causes:
                lines.append(f"- {truncate(cause, 200)} (seen in {count} error(s))")
            content_parts.append("### Top Root Causes\n\n" + "\n".join(lines))

        # Recommendations
        ranked_recs = _rank_items(all_recommendations, limit=5)
        if ranked_recs:
            lines = []
            for rec, count in ranked_recs:
                lines.append(f"- {truncate(rec, 200)} (suggested {count} time(s))")
            content_parts.append("### Top Recommendations\n\n" + "\n".join(lines))

        # Impact distribution
        if impact_counts:
            pairs = [
                (level, str(count)) for level, count in impact_counts.most_common()
            ]
            content_parts.append(
                "### Error Impact Distribution\n\n" + key_value_block(pairs)
            )

        # Score breakdown
        avg_fg = _avg(fg_scores)
        avg_ps = _avg(ps_scores)
        avg_ia = _avg(ia_scores)
        avg_ope = _avg(ope_scores)

        score_pairs = []
        if avg_fg is not None:
            score_pairs.append(("Factual Grounding", format_number(avg_fg)))
        if avg_ps is not None:
            score_pairs.append(("Privacy & Safety", format_number(avg_ps)))
        if avg_ia is not None:
            score_pairs.append(("Instruction Adherence", format_number(avg_ia)))
        if avg_ope is not None:
            score_pairs.append(("Optimal Plan Execution", format_number(avg_ope)))

        if score_pairs:
            content_parts.append(
                "### Average Scores Across Traces\n\n" + key_value_block(score_pairs)
            )

        # Temporal pattern
        if weekday_counts:
            top_days = weekday_counts.most_common(3)
            day_str = ", ".join(f"{day} ({count})" for day, count in top_days)
            content_parts.append(
                "### Temporal Pattern\n\n" f"**Most active days:** {day_str}\n"
            )
            if hour_counts:
                top_hours = hour_counts.most_common(3)
                hour_str = ", ".join(f"{h:02d}:00 ({count})" for h, count in top_hours)
                content_parts[-1] += f"**Most active hours (UTC):** {hour_str}"

        # Evidence samples (deduplicated, limited)
        seen_snippets: set[str] = set()
        unique_evidence: list[tuple[str, str]] = []
        for snippet, tid in all_evidence:
            snippet_key = snippet[:100].lower()
            if snippet_key not in seen_snippets:
                seen_snippets.add(snippet_key)
                unique_evidence.append((snippet, tid))
            if len(unique_evidence) >= 5:
                break

        if unique_evidence:
            lines = []
            for snippet, tid in unique_evidence:
                lines.append(f'- "{truncate(snippet, 200)}" (trace: `{tid[:8]}…`)')
            content_parts.append("### Evidence Samples\n\n" + "\n".join(lines))

        # Per-trace summary table
        trace_rows = []
        trace_data_list = []
        for tid in trace_ids:
            tid_str = str(tid)
            analysis = analysis_by_trace.get(tid_str)
            details = details_by_trace.get(tid_str, [])

            score_val = (
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
            # Pick the top root cause for this trace's errors
            top_cause = "—"
            for d in details:
                if d.root_causes:
                    top_cause = truncate(str(d.root_causes[0]), 60)
                    break

            trace_rows.append(
                [
                    trace_link,
                    score_val,
                    str(len(details)),
                    top_cause,
                    format_datetime(analysis.analysis_date) if analysis else "—",
                ]
            )

            trace_data_list.append(
                {
                    "trace_id": tid_str,
                    "score": (
                        float(analysis.overall_score)
                        if analysis and analysis.overall_score is not None
                        else None
                    ),
                    "error_count": len(details),
                }
            )

        if trace_rows:
            content_parts.append(
                "### Per-Trace Summary\n\n"
                + markdown_table(
                    ["Trace", "Score", "Errors", "Top Root Cause", "Analyzed"],
                    trace_rows,
                )
            )

        content = "\n\n".join(content_parts)

        # ── Structured data ─────────────────────────────────────────
        data = {
            "cluster_id": cluster.cluster_id,
            "error_name": error_name,
            "error_category": category,
            "impact": impact,
            "traces_analyzed": len(trace_ids),
            "total_errors": len(error_details),
            "score_avg": _avg(scores),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "top_root_causes": [
                {"cause": cause, "count": count} for cause, count in ranked_causes
            ],
            "top_recommendations": [
                {"recommendation": rec, "count": count} for rec, count in ranked_recs
            ],
            "traces": trace_data_list,
        }

        return ToolResult(content=content, data=data)
