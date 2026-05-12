from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetOptimizationStepsInput(PydanticBaseModel):
    optimization_id: str = Field(
        default="",
        description="Optimization run UUID or name. If omitted, recent candidates are returned.",
    )


@register_tool
class GetOptimizationStepsTool(BaseTool):
    name = "get_optimization_steps"
    description = (
        "Returns the step-by-step progress of an optimization run. "
        "Shows 4 stages: initialization, baseline eval, optimization trials, "
        "and finalization — each with status and timestamps."
    )
    category = "optimization"
    input_model = GetOptimizationStepsInput

    def execute(
        self, params: GetOptimizationStepsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
        from model_hub.models.optimize_dataset import OptimizeDataset

        from ai_tools.resolvers import is_uuid

        optimization_ref = str(params.optimization_id or "").strip()
        qs = OptimizeDataset.objects.select_related("column", "column__dataset")
        if context.organization:
            qs = qs.filter(column__dataset__organization=context.organization)

        def candidate_runs_result(title: str, detail: str = "") -> ToolResult:
            runs = list(qs.order_by("-created_at")[:10])
            rows = []
            data = []
            for candidate in runs:
                dataset = candidate.column.dataset if candidate.column else None
                rows.append(
                    [
                        f"`{candidate.id}`",
                        truncate(candidate.name, 36),
                        format_status(candidate.status),
                        dataset.name if dataset else "—",
                        format_datetime(candidate.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(candidate.id),
                        "name": candidate.name,
                        "status": candidate.status,
                    }
                )
            body = detail or "Provide `optimization_id` to inspect optimization steps."
            if rows:
                body += "\n\n" + markdown_table(
                    ["ID", "Name", "Status", "Dataset", "Created"],
                    rows,
                )
            else:
                body += "\n\nNo optimization runs found in this workspace."
            return ToolResult(
                content=section(title, body),
                data={"requires_optimization_id": True, "runs": data},
            )

        if not optimization_ref:
            return candidate_runs_result("Optimization Run Required")

        if is_uuid(optimization_ref):
            run = qs.filter(id=optimization_ref).first()
        else:
            run = qs.filter(name__iexact=optimization_ref).first()
            if run is None:
                run = qs.filter(name__icontains=optimization_ref).first()

        if run is None:
            return candidate_runs_result(
                "Optimization Run Not Found",
                f"No optimization run matched `{optimization_ref}`.",
            )

        steps = DatasetOptimizationStep.objects.filter(optimization_run=run).order_by(
            "step_number"
        )

        if not steps.exists():
            return ToolResult(
                content=section(
                    f"Optimization Steps: {run.name}",
                    "_No steps found. The optimization may not have started yet._",
                ),
                data={"steps": []},
            )

        rows = []
        data_list = []
        for s in steps:
            rows.append(
                [
                    str(s.step_number),
                    s.name or "—",
                    format_status(s.status),
                    truncate(s.description, 50) if s.description else "—",
                    format_datetime(s.updated_at),
                ]
            )
            data_list.append(
                {
                    "step_number": s.step_number,
                    "name": s.name,
                    "status": s.status,
                    "description": s.description,
                }
            )

        table = markdown_table(
            ["Step", "Name", "Status", "Description", "Updated"],
            rows,
        )

        info = key_value_block(
            [
                ("Run", run.name),
                ("Overall Status", format_status(run.status)),
                ("Algorithm", run.optimizer_algorithm or "—"),
            ]
        )

        content = section(f"Optimization Steps: {run.name}", info)
        content += f"\n\n{table}"

        return ToolResult(
            content=content,
            data={"steps": data_list, "run_status": run.status},
        )
