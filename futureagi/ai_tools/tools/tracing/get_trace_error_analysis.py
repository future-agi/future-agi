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
from ai_tools.tools.tracing._error_utils import candidate_error_analysis_traces_result


class GetTraceErrorAnalysisInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="The UUID of the trace to get error analysis for. Omit to list candidates.",
    )


@register_tool
class GetTraceErrorAnalysisTool(BaseTool):
    name = "get_trace_error_analysis"
    description = (
        "Returns the AI-powered error analysis for a trace, including "
        "overall score, individual errors with categories and impact levels, "
        "detailed scores (factual grounding, privacy, instruction adherence, "
        "optimal plan execution), and recommended priorities."
    )
    category = "error_feed"
    input_model = GetTraceErrorAnalysisInput

    def execute(
        self, params: GetTraceErrorAnalysisInput, context: ToolContext
    ) -> ToolResult:
        from django.core.exceptions import ValidationError
        from tracer.models.trace import Trace
        from tracer.models.trace_error_analysis import (
            TraceErrorAnalysis,
            TraceErrorDetail,
        )

        if not params.trace_id:
            return candidate_error_analysis_traces_result(
                context,
                "Trace Required For Error Analysis",
                "Choose one of these trace IDs.",
            )

        # Verify trace exists and belongs to the user's organization
        try:
            trace = Trace.objects.get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except (Trace.DoesNotExist, ValidationError, ValueError, TypeError):
            return candidate_error_analysis_traces_result(
                context,
                "Trace Not Found",
                f"Trace `{params.trace_id}` was not found or is not accessible.",
            )

        # Get latest analysis
        analysis = (
            TraceErrorAnalysis.objects.filter(trace=trace)
            .order_by("-analysis_date")
            .first()
        )

        if not analysis:
            return ToolResult(
                content=section(
                    "Error Analysis",
                    f"No error analysis found for trace `{params.trace_id}`.\n\n"
                    f"Error analysis status: {trace.error_analysis_status}",
                ),
                data={"trace_id": str(params.trace_id), "analysis": None},
            )

        info = key_value_block(
            [
                ("Trace ID", f"`{params.trace_id}`"),
                ("Analysis Date", format_datetime(analysis.analysis_date)),
                (
                    "Overall Score",
                    (
                        format_number(analysis.overall_score)
                        if analysis.overall_score is not None
                        else "—"
                    ),
                ),
                ("Total Errors", str(analysis.total_errors)),
                ("High Impact", str(analysis.high_impact_errors)),
                ("Medium Impact", str(analysis.medium_impact_errors)),
                ("Low Impact", str(analysis.low_impact_errors)),
                ("Priority", analysis.recommended_priority),
            ]
        )

        content = section("Error Analysis", info)

        # Detailed scores
        score_pairs = []
        if analysis.factual_grounding_score is not None:
            score_pairs.append(
                ("Factual Grounding", format_number(analysis.factual_grounding_score))
            )
        if analysis.privacy_and_safety_score is not None:
            score_pairs.append(
                ("Privacy & Safety", format_number(analysis.privacy_and_safety_score))
            )
        if analysis.instruction_adherence_score is not None:
            score_pairs.append(
                (
                    "Instruction Adherence",
                    format_number(analysis.instruction_adherence_score),
                )
            )
        if analysis.optimal_plan_execution_score is not None:
            score_pairs.append(
                (
                    "Optimal Plan Execution",
                    format_number(analysis.optimal_plan_execution_score),
                )
            )

        if score_pairs:
            content += "\n\n### Detailed Scores\n\n"
            content += key_value_block(score_pairs)

        # Score reasons
        reasons = []
        if analysis.factual_grounding_reason:
            reasons.append(("Factual Grounding", analysis.factual_grounding_reason))
        if analysis.privacy_and_safety_reason:
            reasons.append(("Privacy & Safety", analysis.privacy_and_safety_reason))
        if analysis.instruction_adherence_reason:
            reasons.append(
                ("Instruction Adherence", analysis.instruction_adherence_reason)
            )
        if analysis.optimal_plan_execution_reason:
            reasons.append(
                ("Optimal Plan Execution", analysis.optimal_plan_execution_reason)
            )

        if reasons:
            content += "\n\n### Score Explanations\n\n"
            for name, reason in reasons:
                content += f"**{name}:** {truncate(reason, 200)}\n\n"

        # Individual errors
        errors = TraceErrorDetail.objects.filter(analysis=analysis).order_by("error_id")
        if errors:
            content += "\n\n### Errors Found\n\n"
            error_rows = []
            error_data = []
            for err in errors[:20]:
                error_rows.append(
                    [
                        err.error_id,
                        err.impact,
                        err.urgency_to_fix,
                        truncate(err.category, 50),
                        str(len(err.location_spans)) if err.location_spans else "0",
                    ]
                )
                error_data.append(
                    {
                        "error_id": err.error_id,
                        "impact": err.impact,
                        "urgency": err.urgency_to_fix,
                        "category": err.category,
                        "location_spans": err.location_spans,
                        "evidence_snippets": err.evidence_snippets,
                    }
                )
            content += markdown_table(
                ["Error ID", "Impact", "Urgency", "Category", "Spans"], error_rows
            )
        else:
            error_data = []

        # Insights
        if analysis.insights:
            content += f"\n\n### Insights\n\n{truncate(analysis.insights, 500)}"

        data = {
            "trace_id": str(params.trace_id),
            "overall_score": (
                float(analysis.overall_score)
                if analysis.overall_score is not None
                else None
            ),
            "total_errors": analysis.total_errors,
            "recommended_priority": analysis.recommended_priority,
            "errors": error_data,
        }

        return ToolResult(content=content, data=data)
