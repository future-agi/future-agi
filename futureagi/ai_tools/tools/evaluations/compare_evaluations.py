from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class CompareEvaluationsInput(PydanticBaseModel):
    evaluation_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "List of 2-5 evaluation IDs to compare side-by-side. Omit to list "
            "candidate evaluations with IDs."
        ),
    )


@register_tool
class CompareEvaluationsTool(BaseTool):
    name = "compare_evaluations"
    description = (
        "Compares multiple evaluations side-by-side. Shows status, scores, "
        "models, and metrics for each evaluation to help identify differences."
    )
    category = "evaluations"
    input_model = CompareEvaluationsInput

    def execute(
        self, params: CompareEvaluationsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.evaluation import Evaluation

        if not params.evaluation_ids or len(params.evaluation_ids) < 2:
            candidates = list(
                Evaluation.objects.select_related("eval_template")
                .filter(organization=context.organization)
                .order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for ev in candidates:
                template_name = ev.eval_template.name if ev.eval_template else "—"
                rows.append(
                    [
                        f"`{ev.id}`",
                        truncate(template_name, 32),
                        format_status(ev.status),
                        truncate(ev.value, 30) if ev.value else "—",
                        format_datetime(ev.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(ev.id),
                        "template_name": template_name,
                        "status": ev.status,
                        "value": str(ev.value) if ev.value else None,
                    }
                )

            detail = (
                "Provide at least two `evaluation_ids` from the candidates below, "
                "then call `compare_evaluations` again."
            )
            if not rows:
                detail = (
                    "No evaluation results were found in this workspace. Run an "
                    "evaluation first, then call `compare_evaluations` with two "
                    "or more evaluation IDs."
                )
            return ToolResult.needs_input(
                section(
                    "Evaluation IDs Required",
                    f"{detail}\n\n"
                    + markdown_table(
                        ["ID", "Template", "Status", "Value", "Created"], rows
                    ),
                ),
                data={"candidate_evaluations": data},
                missing_fields=["evaluation_ids"],
            )

        evals = []
        missing = []
        for eid in params.evaluation_ids[:5]:
            try:
                ev = Evaluation.objects.select_related("eval_template").get(
                    id=eid, organization=context.organization
                )
                evals.append(ev)
            except Evaluation.DoesNotExist:
                missing.append(str(eid))

        if missing:
            return ToolResult.error(
                f"Evaluations not found: {', '.join(missing)}",
                error_code="NOT_FOUND",
            )

        # Build comparison table — each eval is a column
        headers = ["Attribute"] + [
            truncate(ev.eval_template.name if ev.eval_template else str(ev.id), 20)
            for ev in evals
        ]

        rows = []

        # Row: ID
        rows.append(["ID"] + [f"`{str(ev.id)}`" for ev in evals])

        # Row: Template
        rows.append(
            ["Template"]
            + [ev.eval_template.name if ev.eval_template else "—" for ev in evals]
        )

        # Row: Status
        rows.append(["Status"] + [format_status(ev.status) for ev in evals])

        # Row: Model
        rows.append(["Model"] + [ev.model_name or "—" for ev in evals])

        # Row: Value/Score
        rows.append(
            ["Value"] + [truncate(ev.value, 30) if ev.value else "—" for ev in evals]
        )

        # Row: Output Float (if any have it)
        if any(ev.output_float is not None for ev in evals):
            rows.append(
                ["Score (float)"]
                + [
                    (
                        format_number(ev.output_float)
                        if ev.output_float is not None
                        else "—"
                    )
                    for ev in evals
                ]
            )

        # Row: Output Bool (if any have it)
        if any(ev.output_bool is not None for ev in evals):
            rows.append(
                ["Result (bool)"]
                + [
                    str(ev.output_bool) if ev.output_bool is not None else "—"
                    for ev in evals
                ]
            )

        # Row: Runtime
        rows.append(
            ["Runtime"]
            + [f"{format_number(ev.runtime)}s" if ev.runtime else "—" for ev in evals]
        )

        # Row: Created
        rows.append(["Created"] + [format_datetime(ev.created_at) for ev in evals])

        # Row: Metrics (if present as JSON)
        all_metric_keys = set()
        for ev in evals:
            if ev.metrics and isinstance(ev.metrics, dict):
                all_metric_keys.update(ev.metrics.keys())

        for key in sorted(all_metric_keys):
            row = [f"Metric: {key}"]
            for ev in evals:
                val = (
                    ev.metrics.get(key)
                    if ev.metrics and isinstance(ev.metrics, dict)
                    else None
                )
                row.append(format_number(val) if val is not None else "—")
            rows.append(row)

        table = markdown_table(headers, rows)
        content = section(f"Evaluation Comparison ({len(evals)} evaluations)", table)

        # Links
        links = "\n".join(
            [
                f"- {dashboard_link('evaluation', str(ev.id), label=ev.eval_template.name if ev.eval_template else str(ev.id))}"
                for ev in evals
            ]
        )
        content += f"\n\n### Dashboard Links\n\n{links}"

        data = {
            "evaluation_ids": [str(ev.id) for ev in evals],
            "count": len(evals),
        }

        return ToolResult(content=content, data=data)
