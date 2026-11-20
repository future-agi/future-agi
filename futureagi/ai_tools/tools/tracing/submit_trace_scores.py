from typing import Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_number, key_value_block, section
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class SubmitTraceScoresInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace to score")
    overall_score: float = Field(
        ge=1.0,
        le=5.0,
        description="Overall quality score (1-5). Most traces should be 2-4.",
    )
    factual_grounding_score: float = Field(
        ge=1.0,
        le=5.0,
        description="Accuracy and truthfulness score (1-5)",
    )
    factual_grounding_reason: str = Field(
        description="Explanation for the factual grounding score",
    )
    privacy_and_safety_score: float = Field(
        ge=1.0,
        le=5.0,
        description="Security and ethical compliance score (1-5)",
    )
    privacy_and_safety_reason: str = Field(
        description="Explanation for the privacy and safety score",
    )
    instruction_adherence_score: float = Field(
        ge=1.0,
        le=5.0,
        description="How well the agent followed user instructions (1-5)",
    )
    instruction_adherence_reason: str = Field(
        description="Explanation for the instruction adherence score",
    )
    optimal_plan_execution_score: float = Field(
        ge=1.0,
        le=5.0,
        description="Efficiency and correctness of execution (1-5)",
    )
    optimal_plan_execution_reason: str = Field(
        description="Explanation for the optimal plan execution score",
    )
    insights: str = Field(
        description="Key takeaways about this trace's quality",
    )
    recommended_priority: str = Field(
        description="Overall priority: HIGH, MEDIUM, or LOW",
    )
    analysis_id: Optional[UUID] = Field(
        default=None,
        description=(
            "The analysis_id returned by submit_trace_finding. "
            "Pass this to update the correct analysis record."
        ),
    )


@register_tool
class SubmitTraceScoresTool(BaseTool):
    name = "submit_trace_scores"
    description = (
        "Submits quality scores for a trace across 4 dimensions: factual grounding, "
        "privacy & safety, instruction adherence, and optimal plan execution. "
        "Call this AFTER submitting all findings with submit_trace_finding. "
        "Pass the analysis_id from submit_trace_finding to update the correct record. "
        "This finalizes the analysis and triggers error clustering."
    )
    category = "error_feed"
    input_model = SubmitTraceScoresInput

    def execute(
        self, params: SubmitTraceScoresInput, context: ToolContext
    ) -> ToolResult:
        from ai_tools.tools.tracing.access import get_trace_project
        from tracer.models.trace_error_analysis import TraceErrorAnalysis
        from tracer.queries import deep_analysis_state

        # Validate priority
        priority = params.recommended_priority.upper()
        if priority not in ("HIGH", "MEDIUM", "LOW"):
            return ToolResult.validation_error(
                f"Invalid recommended_priority '{params.recommended_priority}'. "
                "Must be HIGH, MEDIUM, or LOW."
            )

        project = get_trace_project(params.trace_id, context)
        if project is None:
            return ToolResult.not_found("Trace", str(params.trace_id))

        # Both the explicit ID and fallback must use the checked project.
        analyses = TraceErrorAnalysis.objects.filter(
            trace_id=params.trace_id,
            project=project,
            project__organization=context.organization,
        )
        analysis = (
            analyses.filter(id=params.analysis_id).first()
            if params.analysis_id
            else None
        )
        if analysis is None:
            analysis = analyses.order_by("-analysis_date").first()

        # If still no analysis, create one (scores-only, no findings)
        if analysis is None:
            analysis = TraceErrorAnalysis.objects.create(
                trace_id=params.trace_id,
                project=project,
                overall_score=params.overall_score,
                total_errors=0,
                recommended_priority=priority,
                agent_version="falcon-skill-1.0",
            )

        # Update scores
        analysis.overall_score = params.overall_score
        analysis.factual_grounding_score = params.factual_grounding_score
        analysis.factual_grounding_reason = params.factual_grounding_reason
        analysis.privacy_and_safety_score = params.privacy_and_safety_score
        analysis.privacy_and_safety_reason = params.privacy_and_safety_reason
        analysis.instruction_adherence_score = params.instruction_adherence_score
        analysis.instruction_adherence_reason = params.instruction_adherence_reason
        analysis.optimal_plan_execution_score = params.optimal_plan_execution_score
        analysis.optimal_plan_execution_reason = params.optimal_plan_execution_reason
        analysis.insights = params.insights
        analysis.recommended_priority = priority

        analysis.save(
            update_fields=[
                "overall_score",
                "factual_grounding_score",
                "factual_grounding_reason",
                "privacy_and_safety_score",
                "privacy_and_safety_reason",
                "instruction_adherence_score",
                "instruction_adherence_reason",
                "optimal_plan_execution_score",
                "optimal_plan_execution_reason",
                "insights",
                "recommended_priority",
            ]
        )

        # Background jobs finalize only after the complete agent stream succeeds.
        clustering_triggered = False
        if context.project is None:
            # Done derives from the TraceErrorAnalysis row above; release the marker.
            deep_analysis_state.clear(str(params.trace_id), project_id=str(project.id))

            # Trigger embedding ingestion for this trace's errors
            try:
                from tracer.queries.error_analysis import TraceErrorAnalysisDB

                db = TraceErrorAnalysisDB()
                db.ingest_trace_error_embeddings(
                    str(params.trace_id),
                    project_id=str(project.id),
                    organization_id=str(context.organization.id),
                )
            except Exception as e:
                logger.warning(
                    "embedding_ingestion_failed",
                    trace_id=str(params.trace_id),
                    error=str(e),
                )

            # Trigger async clustering for the project
            try:
                from tracer.tasks.error_analysis import cluster_project_errors

                cluster_project_errors.delay(str(project.id))
                clustering_triggered = True
            except Exception as e:
                logger.warning(
                    "clustering_trigger_failed",
                    project_id=str(project.id),
                    error=str(e),
                )

        # Format response
        info = key_value_block(
            [
                ("Status", "Scores Saved"),
                ("Analysis ID", f"`{analysis.id}`"),
                ("Trace", f"`{params.trace_id}`"),
                ("Overall Score", format_number(params.overall_score)),
                ("Factual Grounding", format_number(params.factual_grounding_score)),
                ("Privacy & Safety", format_number(params.privacy_and_safety_score)),
                (
                    "Instruction Adherence",
                    format_number(params.instruction_adherence_score),
                ),
                (
                    "Optimal Plan Execution",
                    format_number(params.optimal_plan_execution_score),
                ),
                ("Priority", priority),
                ("Errors Found", str(analysis.total_errors)),
            ]
        )

        return ToolResult(
            content=section("Trace Scores Submitted", info),
            data={
                "status": "accepted",
                "analysis_id": str(analysis.id),
                "overall_score": params.overall_score,
                "total_errors": analysis.total_errors,
                "clustering_triggered": clustering_triggered,
            },
        )
