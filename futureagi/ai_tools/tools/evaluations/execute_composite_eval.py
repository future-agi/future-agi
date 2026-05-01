from typing import Any, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool


class ExecuteCompositeEvalInput(PydanticBaseModel):
    composite_eval_id: str = Field(
        description="UUID of the composite eval template to execute."
    )
    mapping: dict[str, Any] = Field(
        description=(
            "Variable mapping: keys are template variable names, values are the "
            "data to evaluate. Example: {'input': 'user query text', 'output': 'model response'}"
        )
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the model for all child evals (optional).",
    )
    row_context: Optional[dict] = Field(
        default=None,
        description="Full row context as key-value dict (for data injection).",
    )
    span_context: Optional[dict] = Field(
        default=None,
        description="Span context for trace-aware evals.",
    )
    trace_context: Optional[dict] = Field(
        default=None,
        description="Trace context for trace-aware evals.",
    )


@register_tool
class ExecuteCompositeEvalTool(BaseTool):
    name = "execute_composite_eval"
    description = (
        "Executes a composite evaluation: runs all child evals on the provided data "
        "and aggregates their scores. Returns per-child results plus the aggregate score. "
        "Use create_composite_eval to create one first, or list_eval_templates to find existing ones."
    )
    category = "evaluations"
    input_model = ExecuteCompositeEvalInput

    def execute(
        self, params: ExecuteCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
        from model_hub.utils.composite_execution import (
            execute_composite_children_sync,
        )

        # ── 1. Load composite template (by name or UUID) ──
        from ai_tools.resolvers import resolve_eval_template

        parent, err = resolve_eval_template(
            params.composite_eval_id, context.organization
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")
        if getattr(parent, "template_type", "single") != "composite":
            return ToolResult.error(
                f"'{parent.name}' is not a composite eval.",
                error_code="VALIDATION_ERROR",
            )

        # ── 2. Load children ──
        child_links = list(
            CompositeEvalChild.objects.filter(parent=parent, deleted=False)
            .select_related("child", "pinned_version")
            .order_by("order")
        )
        if not child_links:
            return ToolResult.error(
                "Composite eval has no children.",
                error_code="VALIDATION_ERROR",
            )

        org = context.organization
        workspace = context.workspace

        # ── 3. Execute ──
        try:
            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=child_links,
                mapping=params.mapping,
                config={},
                org=org,
                workspace=workspace,
                model=params.model,
                input_data_types={},
                row_context=params.row_context,
                span_context=params.span_context,
                trace_context=params.trace_context,
                session_context=None,
                call_context=None,
                error_localizer=False,
                source="falcon_tool",
            )
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Composite execution failed: {str(e)}",
                error_code=code_from_exception(e),
            )

        # ── 4. Build response ──
        completed = sum(1 for cr in outcome.child_results if cr.status == "completed")
        failed = sum(1 for cr in outcome.child_results if cr.status == "failed")

        children_lines = []
        for cr in outcome.child_results:
            status_icon = "Pass" if cr.status == "completed" and cr.score and cr.score >= 0.5 else "Fail"
            if cr.status == "failed":
                status_icon = "Error"
            score_str = f"{cr.score:.2f}" if cr.score is not None else "—"
            reason_preview = truncate(cr.reason or "", 120) if cr.reason else ""
            children_lines.append(
                f"  - **{cr.child_name}**: {status_icon} (score: {score_str})"
                + (f"\n    {reason_preview}" if reason_preview else "")
            )

        agg_str = "—"
        if outcome.aggregate_score is not None:
            agg_str = f"{outcome.aggregate_score:.2f}"
        agg_pass = "—"
        if outcome.aggregate_pass is not None:
            agg_pass = "Pass" if outcome.aggregate_pass else "Fail"

        info = key_value_block(
            [
                ("Composite", f"{parent.name} (`{parent.id}`)"),
                ("Aggregate Score", agg_str),
                ("Aggregate Result", agg_pass),
                ("Aggregation", parent.aggregation_function if parent.aggregation_enabled else "disabled"),
                ("Children", f"{completed} completed, {failed} failed out of {len(outcome.child_results)}"),
            ]
        )

        content = section("Composite Eval Result", info)
        content += "\n\n### Per-Child Results\n\n" + "\n".join(children_lines)

        if outcome.summary:
            content += f"\n\n### Summary\n\n{truncate(outcome.summary, 500)}"

        return ToolResult(
            content=content,
            data={
                "composite_id": str(parent.id),
                "composite_name": parent.name,
                "aggregate_score": outcome.aggregate_score,
                "aggregate_pass": outcome.aggregate_pass,
                "total_children": len(outcome.child_results),
                "completed_children": completed,
                "failed_children": failed,
                "children": [
                    {
                        "name": cr.child_name,
                        "score": cr.score,
                        "output": cr.output,
                        "status": cr.status,
                        "reason": truncate(cr.reason or "", 300) if cr.reason else None,
                    }
                    for cr in outcome.child_results
                ],
            },
        )
