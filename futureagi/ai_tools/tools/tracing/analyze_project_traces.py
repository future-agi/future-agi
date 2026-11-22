"""Request on-demand Falcon trace analysis. Reject before queueing if unavailable."""

from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool


class AnalyzeProjectTracesInput(PydanticBaseModel):
    project_id: UUID = Field(
        description="The project UUID whose traces should be analyzed",
    )
    trace_ids: list[str] = Field(
        default_factory=list,
        description="Optional trace IDs for the request; an empty list denotes project scope.",
    )
    max_traces: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of traces to analyze in this batch",
    )


@register_tool
class AnalyzeProjectTracesTool(BaseTool):
    name = "analyze_project_traces"
    description = (
        "Background multi-trace analysis is currently unavailable."
    )
    category = "error_feed"
    input_model = AnalyzeProjectTracesInput

    def execute(
        self, params: AnalyzeProjectTracesInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.falcon_background_analysis import (
            BackgroundAnalysisUnavailable,
            require_background_analysis_ready,
        )

        try:
            require_background_analysis_ready()
        except BackgroundAnalysisUnavailable as exc:
            return ToolResult.error(str(exc), error_code=exc.code)
