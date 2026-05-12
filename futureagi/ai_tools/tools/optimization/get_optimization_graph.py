from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetOptimizationGraphInput(PydanticBaseModel):
    optimization_id: UUID = Field(description="The UUID of the optimization run")

    @model_validator(mode="before")
    @classmethod
    def normalize_id_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        if data.get("optimization_id"):
            return data
        for alias in ("optimization_run_id", "run_id", "id"):
            if data.get(alias):
                return {**data, "optimization_id": data[alias]}
        return data


@register_tool
class GetOptimizationGraphTool(BaseTool):
    name = "get_optimization_graph"
    description = (
        "Returns score progression data across trials for an optimization run. "
        "Shows how each evaluation metric's score improved from baseline "
        "through each trial — useful for tracking optimization progress."
    )
    category = "optimization"
    input_model = GetOptimizationGraphInput

    def execute(
        self, params: GetOptimizationGraphInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
        from model_hub.models.optimize_dataset import OptimizeDataset

        try:
            run = OptimizeDataset.objects.get(id=params.optimization_id)
        except OptimizeDataset.DoesNotExist:
            return ToolResult.not_found("Optimization Run", str(params.optimization_id))

        trials = DatasetOptimizationTrial.objects.filter(optimization_run=run).order_by(
            "trial_number"
        )

        if not trials.exists():
            return ToolResult(
                content=section(
                    f"Optimization Graph: {run.name}",
                    "_No trials found yet._",
                ),
                data={"trials": []},
            )

        # Build score progression
        rows = []
        data_list = []
        for t in trials:
            label = "Baseline" if t.is_baseline else f"Trial {t.trial_number}"
            score = (
                format_number(t.average_score) if t.average_score is not None else "—"
            )

            # Calculate change from baseline
            change = "—"
            if not t.is_baseline and t.average_score is not None:
                baseline = trials.filter(is_baseline=True).first()
                if baseline and baseline.average_score and baseline.average_score > 0:
                    pct = (
                        (t.average_score - baseline.average_score)
                        / baseline.average_score
                    ) * 100
                    change = f"{pct:+.1f}%"

            rows.append([label, score, change])
            data_list.append(
                {
                    "trial_number": t.trial_number,
                    "label": label,
                    "is_baseline": t.is_baseline,
                    "average_score": (
                        float(t.average_score) if t.average_score is not None else None
                    ),
                }
            )

        table = markdown_table(["Trial", "Avg Score", "Change vs Baseline"], rows)

        info = key_value_block(
            [
                ("Run", run.name),
                ("Algorithm", run.optimizer_algorithm or "—"),
                (
                    "Baseline",
                    (
                        format_number(run.baseline_score)
                        if run.baseline_score is not None
                        else "—"
                    ),
                ),
                (
                    "Best",
                    (
                        format_number(run.best_score)
                        if run.best_score is not None
                        else "—"
                    ),
                ),
                ("Trials", str(trials.count())),
            ]
        )

        content = section(f"Optimization Progress: {run.name}", info)
        content += f"\n\n### Score Progression\n\n{table}"

        return ToolResult(
            content=content,
            data={"trials": data_list},
        )
