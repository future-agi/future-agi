from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.tracing._error_utils import candidate_error_analysis_traces_result


class SubmitTraceFindingInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="The UUID of the trace this finding belongs to. Omit to list candidates.",
    )
    category: str = Field(
        default="",
        description=(
            "Full taxonomy path, e.g., "
            "'Thinking & Response Issues > Hallucination Errors > Hallucinated Content'"
        ),
    )
    location_spans: list[str] = Field(
        default_factory=list,
        description="List of span IDs where the error was found",
    )
    evidence_snippets: list[str] = Field(
        default_factory=list,
        description="Verbatim quotes from span data that prove the error exists",
    )
    description: str = Field(
        default="",
        description="Clear description of what went wrong",
    )
    impact: str = Field(
        default="",
        description="Error impact level: HIGH, MEDIUM, or LOW",
    )
    root_causes: list[str] = Field(
        default_factory=list,
        description="Root cause(s) — why this error happened",
    )
    recommendation: str = Field(
        default="",
        description="How to fix this error",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence level (0.0-1.0). Must be >= 0.7 to be accepted.",
    )
    urgency_to_fix: str = Field(
        default="HIGH",
        description="Urgency: IMMEDIATE, HIGH, MEDIUM, or LOW",
    )
    immediate_fix: Optional[str] = Field(
        default=None,
        description="Quick fix suggestion if applicable",
    )
    trace_impact: Optional[str] = Field(
        default=None,
        description="How this error affects the overall trace execution",
    )
    analysis_id: Optional[str] = Field(
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

    def execute(
        self, params: SubmitTraceFindingInput, context: ToolContext
    ) -> ToolResult:
        from django.core.exceptions import ValidationError
        from django.db import transaction

        from tracer.models.trace import Trace
        from tracer.models.trace_error_analysis import (
            TraceErrorAnalysis,
            TraceErrorDetail,
        )

        if not params.trace_id:
            return candidate_error_analysis_traces_result(
                context,
                "Trace Required For Finding",
                "Choose a trace ID before submitting a finding.",
            )

        missing = []
        if not params.category:
            missing.append("category")
        if not params.location_spans:
            missing.append("location_spans")
        if not params.evidence_snippets:
            missing.append("evidence_snippets")
        if not params.description:
            missing.append("description")
        if not params.impact:
            missing.append("impact")
        if not params.root_causes:
            missing.append("root_causes")
        if not params.recommendation:
            missing.append("recommendation")
        if missing:
            return ToolResult(
                content=section(
                    "Direct Trace Evidence Required",
                    "Finding was not submitted. Provide direct trace evidence and "
                    f"these missing fields: {', '.join(missing)}.",
                ),
                data={
                    "submitted": False,
                    "requires_fields": missing,
                    "trace_id": str(params.trace_id),
                },
            )

        # Validate confidence threshold
        if params.confidence < 0.7:
            return ToolResult(
                content=section(
                    "Finding Rejected",
                    f"Confidence {params.confidence:.2f} is below the 0.7 threshold. "
                    "Only submit findings you are confident about.",
                ),
                data={"submitted": False, "reason": "confidence_too_low"},
            )

        # Validate impact
        impact_upper = params.impact.upper()
        if impact_upper not in ("HIGH", "MEDIUM", "LOW"):
            return ToolResult.validation_error(
                f"Invalid impact '{params.impact}'. Must be HIGH, MEDIUM, or LOW."
            )

        # Verify trace access
        try:
            trace = Trace.objects.select_related("project").get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except (Trace.DoesNotExist, ValidationError, ValueError, TypeError):
            return candidate_error_analysis_traces_result(
                context,
                "Trace Not Found",
                f"Trace `{params.trace_id}` was not found or is not accessible.",
            )

        with transaction.atomic():
            analysis = None

            # If analysis_id provided, reuse that specific analysis
            if params.analysis_id:
                try:
                    analysis = TraceErrorAnalysis.objects.get(
                        id=params.analysis_id,
                        trace=trace,
                    )
                except TraceErrorAnalysis.DoesNotExist:
                    # Invalid analysis_id — create a new one
                    analysis = None

            # Create a new analysis record (same as the old agent did)
            if analysis is None:
                analysis = TraceErrorAnalysis.objects.create(
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

            # Generate error_id based on existing count in THIS analysis
            existing_count = TraceErrorDetail.objects.filter(analysis=analysis).count()
            error_id = f"E{existing_count + 1:03d}"

            # Create the error detail
            TraceErrorDetail.objects.create(
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
            impact_field = {
                "HIGH": "high_impact_errors",
                "MEDIUM": "medium_impact_errors",
                "LOW": "low_impact_errors",
            }.get(impact_upper)
            if impact_field:
                setattr(analysis, impact_field, getattr(analysis, impact_field) + 1)

            # Update priority based on highest impact seen
            if impact_upper == "HIGH":
                analysis.recommended_priority = "HIGH"
            elif impact_upper == "MEDIUM" and analysis.recommended_priority == "LOW":
                analysis.recommended_priority = "MEDIUM"

            analysis.save(
                update_fields=[
                    "total_errors",
                    "high_impact_errors",
                    "medium_impact_errors",
                    "low_impact_errors",
                    "recommended_priority",
                ]
            )

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
                "error_id": error_id,
                "analysis_id": str(analysis.id),
                "total_errors": analysis.total_errors,
            },
        )
