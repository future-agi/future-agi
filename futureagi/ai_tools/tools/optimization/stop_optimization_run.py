from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class StopOptimizationRunInput(PydanticBaseModel):
    optimization_id: str = Field(
        default="",
        description="The UUID or exact name of the optimization run to stop. Omit to list candidates.",
    )


@register_tool
class StopOptimizationRunTool(BaseTool):
    name = "stop_optimization_run"
    description = (
        "Cancels a running or pending optimization run. "
        "The Temporal workflow will be cancelled and the run marked as cancelled. "
        "Already-completed trials are preserved."
    )
    category = "optimization"
    input_model = StopOptimizationRunInput

    def execute(
        self, params: StopOptimizationRunInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.tools.optimization._utils import resolve_optimization_run

        run, unresolved = resolve_optimization_run(
            params.optimization_id,
            context,
            title="Optimization Run Required To Stop",
        )
        if unresolved:
            return unresolved

        if run.status not in ("running", "pending"):
            return ToolResult.blocked(
                f"Cannot stop optimization with status '{run.status}'. "
                "Only running or pending runs can be stopped.",
                data={"optimization_id": str(run.id), "status": run.status},
                reason="not_running",
            )

        # Try Temporal cancellation
        cancelled = False
        try:
            from asgiref.sync import async_to_sync

            from tfc.temporal.dataset_optimization.client import (
                cancel_dataset_optimization,
            )

            cancelled = async_to_sync(cancel_dataset_optimization)(str(run.id))
        except Exception:
            pass

        # Mark as cancelled in DB regardless
        run.mark_as_cancelled()

        info = key_value_block(
            [
                ("Run", run.name),
                ("ID", f"`{run.id}`"),
                ("Status", "cancelled"),
                ("Workflow Cancelled", "Yes" if cancelled else "Marked in DB only"),
            ]
        )

        return ToolResult(
            content=section("Optimization Stopped", info),
            data={
                "optimization_id": str(run.id),
                "status": "cancelled",
                "workflow_cancelled": cancelled,
            },
        )
