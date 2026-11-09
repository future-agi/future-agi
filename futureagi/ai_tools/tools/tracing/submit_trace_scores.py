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
    analysis_id: UUID | None = Field(
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

    def run(self, raw_params, context):
        if getattr(context, "background_analysis", None) is not None:
            from ee.agenthub.traceerroragent.background_payload_tools import (
                run_background_tool,
            )

            return run_background_tool(self, raw_params, context)
        return super().run(raw_params, context)

    def execute(
        self, params: SubmitTraceScoresInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.analysis_result_binding import (
            ResultOwnershipError,
            current_attempt,
            legacy_attempt,
        )

        guard = getattr(context, "background_analysis", None)
        if guard is not None:
            from ee.agenthub.traceerroragent.background_payload_tools import (
                require_tool_parameters,
            )

            require_tool_parameters(params, guard, "submit_trace_scores")
        if current_attempt() is not None:
            return self._execute_owned(params, context)
        if guard is not None:
            from ee.agenthub.traceerroragent.background_tools import reject_chauffeur

            reject_chauffeur(guard)
        from tracer.models.project import Project
        from tracer.services.clickhouse.v2 import get_reader

        with get_reader() as reader:
            root = reader.root_ids_by_trace_ids([str(params.trace_id)]).get(
                str(params.trace_id)
            )
        project = (
            Project.objects.filter(
                id=root[1], organization=context.organization
            ).first()
            if root
            else None
        )
        if project is None:
            return ToolResult.not_found("Trace", str(params.trace_id))
        try:
            with legacy_attempt(project.pk, params.trace_id):
                result = self._execute_owned(params, context)
                if result.is_error:
                    raise ResultOwnershipError("analysis_failed")
            result.data.pop("analysis_status", None)
            # All exports select through the bound-only reader manager.
            try:
                from tracer.queries.error_analysis import TraceErrorAnalysisDB
                from tracer.tasks.error_analysis import cluster_project_errors

                TraceErrorAnalysisDB().ingest_trace_error_embeddings(
                    str(params.trace_id)
                )
                if guard is None:
                    cluster_project_errors.delay(str(project.id))
                    result.data["clustering_triggered"] = True
            except Exception:
                logger.warning(
                    "analysis_completed_export_failed", trace_id=str(params.trace_id)
                )
            return result
        except ResultOwnershipError:
            return ToolResult.validation_error(
                "A generation-owned analysis task is required."
            )

    def _execute_owned(
        self, params: SubmitTraceScoresInput, context: ToolContext
    ) -> ToolResult:
        from tracer.models.project import Project
        from tracer.models.trace_error_analysis import TraceErrorAnalysis
        from tracer.queries import deep_analysis_state
        from tracer.services.clickhouse.v2 import get_reader

        guard = getattr(context, "background_analysis", None)
        if guard is not None:
            from ee.agenthub.traceerroragent.background_payload_tools import (
                require_tool_parameters,
            )

            require_tool_parameters(params, guard, "submit_trace_scores")

        def checkpoint():
            if guard is not None:
                guard.check()

        from tracer.services.analysis_result_binding import current_attempt

        checkpoint()
        if guard is not None:
            from ee.agenthub.traceerroragent.background_tool_validation import (
                validate_submission,
            )

            validate_submission(params, guard, scores=True)
        # Validate priority
        priority = params.recommended_priority.upper()
        if priority not in ("HIGH", "MEDIUM", "LOW"):
            if guard is not None:
                from ee.agenthub.traceerroragent.background_tools import (
                    reject_chauffeur,
                )

                reject_chauffeur(guard)
            return ToolResult.validation_error(
                f"Invalid recommended_priority '{params.recommended_priority}'. "
                "Must be HIGH, MEDIUM, or LOW."
            )

        # Verify trace access. The trace lives only in CH post-cutover; resolve
        # its project from the CH root span and check org access via the PG
        # Project (kept).
        with get_reader() as reader:
            checkpoint()
            roots = reader.root_ids_by_trace_ids([str(params.trace_id)])
        checkpoint()
        root = roots.get(str(params.trace_id))
        project_id = root[1] if root else None
        project = (
            Project.objects.filter(
                id=project_id, organization=context.organization
            ).first()
            if project_id
            else None
        )
        checkpoint()
        if project is None:
            if guard is not None:
                from ee.agenthub.traceerroragent.background_tools import (
                    reject_chauffeur,
                )

                reject_chauffeur(guard)
            return ToolResult.not_found("Trace", str(params.trace_id))

        # Find the analysis record
        analysis = None
        if params.analysis_id:
            try:
                analysis = TraceErrorAnalysis.drafts.get(
                    id=params.analysis_id,
                    trace_id=params.trace_id,
                )
            except TraceErrorAnalysis.DoesNotExist:
                if guard is not None:
                    from ee.agenthub.traceerroragent.background_tools import (
                        reject_chauffeur,
                    )

                    reject_chauffeur(guard)
                analysis = None

        checkpoint()
        # Fallback: get the most recent analysis for this trace
        if analysis is None:
            analysis = (
                TraceErrorAnalysis.drafts.filter(trace_id=params.trace_id)
                .order_by("-analysis_date")
                .first()
            )

        checkpoint()
        # If still no analysis, create one (scores-only, no findings)
        if analysis is None:
            analysis = TraceErrorAnalysis.drafts.create(
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

        checkpoint()
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

        checkpoint()
        # Background jobs own their marker until finalization, not tool return.
        if guard is None:
            deep_analysis_state.clear(str(params.trace_id))

        # Drafts are not embedding/clustering input. The owning worker exports
        # only after publication; standalone legacy admission does so below.

        checkpoint()
        # Format response
        info = key_value_block(
            [
                (
                    "Status",
                    "Scores Saved; Publication Pending"
                    if current_attempt()
                    else "Completed",
                ),
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
                "clustering_triggered": False,
                "analysis_status": "running",
            },
        )
