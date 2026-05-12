from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetOptimizationRunInput(PydanticBaseModel):
    optimization_id: str = Field(
        default="",
        description="Optimization run UUID or name. If omitted, recent candidates are returned.",
    )
    include_trials: bool = Field(
        default=True, description="Include trial details with scores and prompts"
    )
    include_steps: bool = Field(
        default=True, description="Include optimization step details"
    )


@register_tool
class GetOptimizationRunTool(BaseTool):
    name = "get_optimization_run"
    description = (
        "Returns detailed information about a prompt optimization run including "
        "its configuration, algorithm, trials with scores and prompts, and "
        "optimization steps. Shows which prompt variant scored best."
    )
    category = "optimization"
    input_model = GetOptimizationRunInput

    def execute(
        self, params: GetOptimizationRunInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
        from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
        from model_hub.models.optimize_dataset import OptimizeDataset
        from ai_tools.resolvers import is_uuid

        def candidate_runs_result(title: str, detail: str = "") -> ToolResult:
            qs = OptimizeDataset.objects.select_related("column", "column__dataset")
            if context.organization:
                qs = qs.filter(column__dataset__organization=context.organization)
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
            body = detail or "Provide `optimization_id` to inspect a run."
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

        optimization_ref = str(params.optimization_id or "").strip()
        if not optimization_ref:
            return candidate_runs_result("Optimization Run Required")

        try:
            qs = OptimizeDataset.objects.select_related("column", "column__dataset")
            if context.organization:
                qs = qs.filter(column__dataset__organization=context.organization)

            if is_uuid(optimization_ref):
                run = qs.get(id=optimization_ref)
            else:
                matches = qs.filter(name__iexact=optimization_ref)
                if matches.count() == 1:
                    run = matches.first()
                elif matches.count() > 1:
                    return candidate_runs_result(
                        "Multiple Optimization Runs Matched",
                        f"More than one optimization run matched `{optimization_ref}`. Use an ID.",
                    )
                else:
                    fuzzy = qs.filter(name__icontains=optimization_ref)
                    if fuzzy.count() == 1:
                        run = fuzzy.first()
                    else:
                        return candidate_runs_result(
                            "Optimization Run Not Found",
                            f"No optimization run matched `{optimization_ref}`.",
                        )
        except OptimizeDataset.DoesNotExist:
            return candidate_runs_result(
                "Optimization Run Not Found",
                f"Optimization run `{optimization_ref}` was not found.",
            )

        dataset_name = "—"
        column_name = "—"
        if run.column:
            column_name = run.column.name
            if run.column.dataset:
                dataset_name = run.column.dataset.name

        algo_config = run.optimizer_config or {}

        info = key_value_block(
            [
                ("ID", f"`{run.id}`"),
                ("Name", run.name),
                ("Status", format_status(run.status)),
                ("Algorithm", run.optimizer_algorithm or "—"),
                ("Dataset", dataset_name),
                ("Column", column_name),
                ("Optimize Type", run.optimize_type or "—"),
                (
                    "Baseline Score",
                    (
                        format_number(run.baseline_score)
                        if run.baseline_score is not None
                        else "—"
                    ),
                ),
                (
                    "Best Score",
                    (
                        format_number(run.best_score)
                        if run.best_score is not None
                        else "—"
                    ),
                ),
                ("Created", format_datetime(run.created_at)),
            ]
        )

        content = section(f"Optimization: {run.name}", info)

        # Algorithm config
        if algo_config:
            content += "\n\n### Algorithm Configuration\n\n"
            for key, val in algo_config.items():
                content += f"- **{key}**: {val}\n"

        # Error message
        if run.error_message:
            content += f"\n\n### Error\n\n```\n{truncate(run.error_message, 500)}\n```"

        # Optimization steps
        step_data = []
        if params.include_steps:
            steps = DatasetOptimizationStep.objects.filter(
                optimization_run=run
            ).order_by("step_number")

            if steps:
                content += "\n\n### Steps\n\n"
                step_rows = []
                for s in steps:
                    step_rows.append(
                        [
                            str(s.step_number),
                            s.name or "—",
                            format_status(s.status),
                            truncate(s.description, 40) if s.description else "—",
                        ]
                    )
                    step_data.append(
                        {
                            "step_number": s.step_number,
                            "name": s.name,
                            "status": s.status,
                            "description": s.description,
                        }
                    )
                content += markdown_table(
                    ["Step", "Name", "Status", "Description"], step_rows
                )

        # Trials
        trial_data = []
        if params.include_trials:
            trials = DatasetOptimizationTrial.objects.filter(
                optimization_run=run
            ).order_by("trial_number")

            if trials:
                content += "\n\n### Trials\n\n"
                trial_rows = []
                for t in trials:
                    is_baseline = "Yes" if t.is_baseline else "No"
                    prompt_preview = truncate(t.prompt, 50) if t.prompt else "—"
                    score = (
                        format_number(t.average_score)
                        if t.average_score is not None
                        else "—"
                    )

                    trial_rows.append(
                        [
                            str(t.trial_number),
                            is_baseline,
                            score,
                            prompt_preview,
                        ]
                    )
                    trial_data.append(
                        {
                            "id": str(t.id),
                            "trial_number": t.trial_number,
                            "is_baseline": t.is_baseline,
                            "average_score": (
                                float(t.average_score)
                                if t.average_score is not None
                                else None
                            ),
                            "prompt": t.prompt,
                        }
                    )
                content += markdown_table(
                    ["Trial", "Baseline", "Avg Score", "Prompt Preview"], trial_rows
                )

        # Best prompts
        if run.optimized_k_prompts:
            content += "\n\n### Best Prompts\n\n"
            for i, prompt in enumerate(run.optimized_k_prompts[:3]):
                content += f"**#{i + 1}:**\n```\n{truncate(prompt, 300)}\n```\n\n"

        data = {
            "id": str(run.id),
            "name": run.name,
            "status": run.status,
            "algorithm": run.optimizer_algorithm,
            "dataset": dataset_name,
            "column": column_name,
            "baseline_score": (
                float(run.baseline_score) if run.baseline_score is not None else None
            ),
            "best_score": float(run.best_score) if run.best_score is not None else None,
            "steps": step_data,
            "trials": trial_data,
            "best_prompts": run.optimized_k_prompts,
        }

        return ToolResult(content=content, data=data)
