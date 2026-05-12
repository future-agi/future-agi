from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.optimization._utils import (
    candidate_trials_result,
    resolve_optimization_run,
    resolve_trial,
)


class GetTrialScenariosInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    optimization_id: str = Field(
        default="",
        description="Optimization run UUID or name. If omitted, candidates are returned.",
    )
    trial_id: str = Field(
        default="",
        description="Trial UUID, trial number, 'baseline', or 'best'.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max rows to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["optimization_id"] = (
            normalized.get("optimization_id")
            or normalized.get("optimization_run_id")
            or normalized.get("run_id")
            or normalized.get("id")
            or ""
        )
        normalized["trial_id"] = (
            normalized.get("trial_id")
            or normalized.get("optimization_trial_id")
            or normalized.get("trial")
            or ""
        )
        return normalized


@register_tool
class GetTrialScenariosTool(BaseTool):
    name = "get_trial_scenarios"
    description = (
        "Returns per-row results for a specific optimization trial. "
        "Shows input text, model output, score, and reason for each dataset row. "
        "Useful for understanding why a prompt performed well or poorly."
    )
    category = "optimization"
    input_model = GetTrialScenariosInput

    def execute(
        self, params: GetTrialScenariosInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.dataset_optimization_trial_item import (
            DatasetOptimizationTrialItem,
        )

        run, unresolved = resolve_optimization_run(params.optimization_id, context)
        if unresolved:
            return unresolved
        trial, error = resolve_trial(params.trial_id, run)
        if error:
            return candidate_trials_result(
                run,
                "Optimization Trial Not Found",
                error,
                search=params.trial_id,
            )

        total = DatasetOptimizationTrialItem.objects.filter(trial=trial).count()
        items = DatasetOptimizationTrialItem.objects.filter(trial=trial).order_by("id")[
            params.offset : params.offset + params.limit
        ]

        if not items:
            trial_label = (
                "Baseline" if trial.is_baseline else f"Trial {trial.trial_number}"
            )
            return ToolResult(
                content=section(
                    f"Trial Scenarios: {trial_label}",
                    "_No row results found._",
                ),
                data={"items": [], "total": 0},
            )

        rows = []
        data_list = []
        for item in items:
            input_preview = (
                truncate(str(item.input_text), 60) if item.input_text else "—"
            )
            output_preview = (
                truncate(str(item.output_text), 60) if item.output_text else "—"
            )
            reason_preview = truncate(item.reason, 40) if item.reason else "—"

            rows.append(
                [
                    input_preview,
                    output_preview,
                    format_number(item.score) if item.score is not None else "—",
                    reason_preview,
                ]
            )
            data_list.append(
                {
                    "id": str(item.id),
                    "input_text": str(item.input_text) if item.input_text else None,
                    "output_text": str(item.output_text) if item.output_text else None,
                    "score": float(item.score) if item.score is not None else None,
                    "reason": item.reason,
                }
            )

        table = markdown_table(["Input", "Output", "Score", "Reason"], rows)

        trial_label = "Baseline" if trial.is_baseline else f"Trial {trial.trial_number}"
        info = key_value_block(
            [
                ("Run", run.name),
                ("Trial", trial_label),
                (
                    "Score",
                    (
                        format_number(trial.average_score)
                        if trial.average_score is not None
                        else "—"
                    ),
                ),
                ("Showing", f"{len(data_list)} of {total} rows"),
            ]
        )

        content = section(f"Trial Scenarios: {trial_label}", info)
        content += f"\n\n{table}"

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more rows._"
            )

        return ToolResult(
            content=content,
            data={"items": data_list, "total": total},
        )
