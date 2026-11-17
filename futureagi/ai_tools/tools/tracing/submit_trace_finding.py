from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class SubmitTraceFindingInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace this finding belongs to")
    category: str = Field(
        description=(
            "Full taxonomy path, e.g., "
            "'Thinking & Response Issues > Hallucination Errors > Hallucinated Content'"
        ),
    )
    location_spans: list[str] = Field(
        description="List of span IDs where the error was found",
    )
    evidence_snippets: list[str] = Field(
        description="Verbatim quotes from span data that prove the error exists",
    )
    description: str = Field(
        description="Clear description of what went wrong",
    )
    impact: str = Field(
        description="Error impact level: HIGH, MEDIUM, or LOW",
    )
    root_causes: list[str] = Field(
        description="Root cause(s) — why this error happened",
    )
    recommendation: str = Field(
        description="How to fix this error",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence level (0.0-1.0). Must be >= 0.7 to be accepted.",
    )
    urgency_to_fix: str = Field(
        default="HIGH",
        description="Urgency: IMMEDIATE, HIGH, MEDIUM, or LOW",
    )
    immediate_fix: str | None = Field(
        default=None,
        description="Quick fix suggestion if applicable",
    )
    trace_impact: str | None = Field(
        default=None,
        description="How this error affects the overall trace execution",
    )
    analysis_id: UUID | None = Field(
        default=None,
        description=(
            "The analysis_id returned by a previous submit_trace_finding call "
            "for this same trace. Pass this to add findings to the same analysis "
            "session. If omitted, a new analysis record is created."
        ),
    )


@register_tool
class SubmitTraceFindingTool(BaseTool):
    name = "submit_trace_finding"
    description = (
        "Submits a detected error finding for a trace. Each call persists one "
        "error to the database immediately. Requires verbatim evidence quotes "
        "from span data and confidence >= 0.7. The first call for a trace creates "
        "a new analysis — pass the returned analysis_id to subsequent calls to "
        "group findings together."
    )
    category = "error_feed"
    input_model = SubmitTraceFindingInput

    def run(self, raw_params, context):
        if getattr(context, "background_analysis", None) is not None:
            from ee.agenthub.traceerroragent.background_payload_tools import (
                run_background_tool,
            )

            return run_background_tool(self, raw_params, context)
        return super().run(raw_params, context)

    def execute(
        self, params: SubmitTraceFindingInput, context: ToolContext
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

            require_tool_parameters(params, guard, "submit_trace_finding")
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
            return result
        except ResultOwnershipError:
            return ToolResult.validation_error(
                "A generation-owned analysis task is required."
            )

    def _execute_owned(
        self, params: SubmitTraceFindingInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction

        from tracer.models.trace import Trace
        from tracer.models.trace_error_analysis import (
            TraceErrorAnalysis,
            TraceErrorDetail,
        )

        guard = getattr(context, "background_analysis", None)
        if guard is not None:
            from ee.agenthub.traceerroragent.background_payload_tools import (
                require_tool_parameters,
            )

            require_tool_parameters(params, guard, "submit_trace_finding")

        def checkpoint():
            if guard is not None:
                guard.check()

        from tracer.services.analysis_result_binding import current_attempt

        checkpoint()
        if guard is not None:
            from ee.agenthub.traceerroragent.background_tool_validation import (
                validate_submission,
            )

            validate_submission(params, guard)
        # Validate confidence threshold
        if params.confidence < 0.7:
            if guard is not None:
                from ee.agenthub.traceerroragent.background_tools import (
                    reject_chauffeur,
                )

                reject_chauffeur(guard)
            return ToolResult(
                content=section(
                    "Finding Rejected",
                    f"Confidence {params.confidence:.2f} is below the 0.7 threshold. "
                    "Only submit findings you are confident about.",
                ),
                data={"status": "rejected", "reason": "confidence_too_low"},
                is_error=True,
                error_code="VALIDATION_ERROR",
            )

        # Validate impact
        impact_upper = params.impact.upper()
        if impact_upper not in ("HIGH", "MEDIUM", "LOW"):
            if guard is not None:
                from ee.agenthub.traceerroragent.background_tools import (
                    reject_chauffeur,
                )

                reject_chauffeur(guard)
            return ToolResult.validation_error(
                f"Invalid impact '{params.impact}'. Must be HIGH, MEDIUM, or LOW."
            )

        # Verify trace access
        try:
            trace = Trace.objects.select_related("project").get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except Trace.DoesNotExist:
            if guard is not None:
                from ee.agenthub.traceerroragent.background_tools import (
                    reject_chauffeur,
                )

                reject_chauffeur(guard)
            return ToolResult.not_found("Trace", str(params.trace_id))

        checkpoint()
        with transaction.atomic():
            checkpoint()
            analysis = None

            # If analysis_id provided, reuse that specific analysis
            if params.analysis_id:
                try:
                    analysis = TraceErrorAnalysis.drafts.get(
                        id=params.analysis_id,
                        trace=trace,
                    )
                except TraceErrorAnalysis.DoesNotExist:
                    if guard is not None:
                        from ee.agenthub.traceerroragent.background_tools import (
                            reject_chauffeur,
                        )

                        reject_chauffeur(guard)
                    # Invalid analysis_id — create a new one (foreground only)
                    analysis = None

            checkpoint()
            # Create a new analysis record (same as the old agent did)
            if analysis is None and current_attempt() is not None:
                analysis = TraceErrorAnalysis.drafts.filter(trace=trace).first()
            if analysis is None:
                analysis = TraceErrorAnalysis.drafts.create(
                    trace=trace,
                    project=trace.project,
                    overall_score=None,
                    total_errors=0,
                    high_impact_errors=0,
                    medium_impact_errors=0,
                    low_impact_errors=0,
                    recommended_priority="LOW",
                    agent_version="falcon-skill-1.0",
                )

            checkpoint()
            # Generate error_id based on existing count in THIS analysis
            existing_count = TraceErrorDetail.drafts.filter(analysis=analysis).count()
            error_id = f"E{existing_count + 1:03d}"

            checkpoint()
            # Create the error detail
            TraceErrorDetail.drafts.create(
                analysis=analysis,
                error_id=error_id,
                category=params.category,
                impact=impact_upper,
                urgency_to_fix=params.urgency_to_fix.upper(),
                location_spans=params.location_spans,
                evidence_snippets=params.evidence_snippets,
                description=params.description,
                root_causes=params.root_causes,
                recommendation=params.recommendation,
                immediate_fix=params.immediate_fix,
                trace_impact=params.trace_impact,
                trace_assessment=None,
                memory_enhanced=False,
            )

            # Update analysis counters
            analysis.total_errors = existing_count + 1
            # Keep background-reachable code free of dynamic attribute access.
            if impact_upper == "HIGH":
                analysis.high_impact_errors += 1
            elif impact_upper == "MEDIUM":
                analysis.medium_impact_errors += 1
            elif impact_upper == "LOW":
                analysis.low_impact_errors += 1

            # Update priority based on highest impact seen
            if impact_upper == "HIGH":
                analysis.recommended_priority = "HIGH"
            elif impact_upper == "MEDIUM" and analysis.recommended_priority == "LOW":
                analysis.recommended_priority = "MEDIUM"

            checkpoint()
            analysis.save(
                update_fields=[
                    "total_errors",
                    "high_impact_errors",
                    "medium_impact_errors",
                    "low_impact_errors",
                    "recommended_priority",
                ]
            )
            # Roll back the native transaction if access/deadline changed in
            # the write itself, before committing and returning success.
            checkpoint()

        checkpoint()
        # Format response
        info = key_value_block(
            [
                ("Status", "Accepted"),
                ("Error ID", error_id),
                ("Analysis ID", f"`{analysis.id}`"),
                ("Category", params.category),
                ("Impact", impact_upper),
                ("Trace", f"`{params.trace_id}`"),
                ("Total Errors So Far", str(analysis.total_errors)),
            ]
        )

        return ToolResult(
            content=section("Finding Submitted", info),
            data={
                "status": "accepted",
                "analysis_status": "running",
                "error_id": error_id,
                "analysis_id": str(analysis.id),
                "total_errors": analysis.total_errors,
            },
        )
