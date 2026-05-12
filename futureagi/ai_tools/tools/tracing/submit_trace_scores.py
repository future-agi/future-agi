from typing import Optional
from uuid import UUID

import structlog
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
from ai_tools.tools.annotation_queues._utils import uuid_text

logger = structlog.get_logger(__name__)


class SubmitTraceScoresInput(PydanticBaseModel):
    trace_id: Optional[str] = Field(
        default=None,
        description="The UUID of the trace to score. Omit to list candidate traces.",
    )
    overall_score: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="Overall quality score (1-5). Most traces should be 2-4.",
    )
    factual_grounding_score: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="Accuracy and truthfulness score (1-5)",
    )
    factual_grounding_reason: Optional[str] = Field(
        default=None,
        description="Explanation for the factual grounding score",
    )
    privacy_and_safety_score: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="Security and ethical compliance score (1-5)",
    )
    privacy_and_safety_reason: Optional[str] = Field(
        default=None,
        description="Explanation for the privacy and safety score",
    )
    instruction_adherence_score: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="How well the agent followed user instructions (1-5)",
    )
    instruction_adherence_reason: Optional[str] = Field(
        default=None,
        description="Explanation for the instruction adherence score",
    )
    optimal_plan_execution_score: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=5.0,
        description="Efficiency and correctness of execution (1-5)",
    )
    optimal_plan_execution_reason: Optional[str] = Field(
        default=None,
        description="Explanation for the optimal plan execution score",
    )
    insights: Optional[str] = Field(
        default=None,
        description="Key takeaways about this trace's quality",
    )
    recommended_priority: Optional[str] = Field(
        default=None,
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
        from tracer.models.trace import Trace, TraceErrorAnalysisStatus
        from tracer.models.trace_error_analysis import TraceErrorAnalysis

        missing = _missing_score_fields(params)
        trace_id = uuid_text(params.trace_id)
        if missing or not trace_id:
            return _trace_score_requirements_result(
                context,
                detail=(
                    "Provide all scoring fields before saving trace scores. "
                    f"Missing: {', '.join(f'`{field}`' for field in missing)}."
                    if missing
                    else "Provide `trace_id` for the trace to score."
                ),
            )

        # Validate priority
        priority = params.recommended_priority.upper()
        if priority not in ("HIGH", "MEDIUM", "LOW"):
            return ToolResult.validation_error(
                f"Invalid recommended_priority '{params.recommended_priority}'. "
                "Must be HIGH, MEDIUM, or LOW."
            )

        # Verify trace access
        try:
            trace = Trace.objects.select_related("project").get(
                id=trace_id,
                project__organization=context.organization,
            )
        except Trace.DoesNotExist:
            return _trace_score_requirements_result(
                context,
                title="Trace Not Found For Scoring",
                detail=f"Trace `{params.trace_id}` was not found in this workspace.",
            )

        # Find the analysis record
        analysis = None
        if params.analysis_id:
            try:
                analysis = TraceErrorAnalysis.objects.get(
                    id=params.analysis_id,
                    trace=trace,
                )
            except TraceErrorAnalysis.DoesNotExist:
                analysis = None

        # Fallback: get the most recent analysis for this trace
        if analysis is None:
            analysis = (
                TraceErrorAnalysis.objects.filter(trace=trace)
                .order_by("-analysis_date")
                .first()
            )

        # If still no analysis, create one (scores-only, no findings)
        if analysis is None:
            analysis = TraceErrorAnalysis.objects.create(
                trace=trace,
                project=trace.project,
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

        # Mark trace analysis as completed
        trace.error_analysis_status = TraceErrorAnalysisStatus.COMPLETED
        trace.save(update_fields=["error_analysis_status"])

        # Trigger embedding ingestion for this trace's errors
        try:
            from tracer.queries.error_analysis import TraceErrorAnalysisDB

            db = TraceErrorAnalysisDB()
            db.ingest_trace_error_embeddings(trace_id)
        except Exception as e:
            logger.warning(
                "embedding_ingestion_failed",
                trace_id=trace_id,
                error=str(e),
            )

        # Trigger async clustering for the project
        try:
            from tracer.tasks.error_analysis import cluster_project_errors

            cluster_project_errors.delay(str(trace.project_id))
        except Exception as e:
            logger.warning(
                "clustering_trigger_failed",
                project_id=str(trace.project_id),
                error=str(e),
            )

        # Format response
        info = key_value_block(
            [
                ("Status", "Scores Saved & Clustering Triggered"),
                ("Analysis ID", f"`{analysis.id}`"),
                ("Trace", f"`{trace_id}`"),
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
                "trace_id": trace_id,
                "overall_score": params.overall_score,
                "total_errors": analysis.total_errors,
                "clustering_triggered": True,
            },
        )


def _missing_score_fields(params: SubmitTraceScoresInput) -> list[str]:
    required_fields = [
        "trace_id",
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
    missing = []
    for field in required_fields:
        value = getattr(params, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def _trace_score_requirements_result(
    context: ToolContext,
    title: str = "Trace Scores Required",
    detail: str = "",
) -> ToolResult:
    from tracer.models.trace import Trace

    traces = (
        Trace.objects.select_related("project")
        .filter(project__organization=context.organization)
        .order_by("-created_at")[:10]
    )
    rows = []
    data = []
    for trace in traces:
        project_name = trace.project.name if trace.project else "-"
        rows.append(
            [
                f"`{trace.id}`",
                truncate(getattr(trace, "name", "") or "-", 42),
                truncate(project_name, 32),
                format_datetime(trace.created_at),
            ]
        )
        data.append(
            {
                "id": str(trace.id),
                "name": getattr(trace, "name", "") or None,
                "project_id": str(trace.project_id) if trace.project_id else None,
            }
        )

    rubric = (
        "Required fields: `trace_id`, `overall_score`, "
        "`factual_grounding_score`, `factual_grounding_reason`, "
        "`privacy_and_safety_score`, `privacy_and_safety_reason`, "
        "`instruction_adherence_score`, `instruction_adherence_reason`, "
        "`optimal_plan_execution_score`, `optimal_plan_execution_reason`, "
        "`insights`, and `recommended_priority` (`HIGH`, `MEDIUM`, or `LOW`)."
    )
    body = (detail + "\n\n" if detail else "") + rubric
    if rows:
        body += "\n\n" + markdown_table(
            ["Trace ID", "Name", "Project", "Created"],
            rows,
        )
    else:
        body += "\n\nNo traces found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={"requires_scores": True, "traces": data},
    )
