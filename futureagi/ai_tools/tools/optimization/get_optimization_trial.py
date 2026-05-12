from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.optimization._utils import (
    candidate_trials_result,
    resolve_optimization_run,
    resolve_trial,
)


class GetOptimizationTrialInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    optimization_id: str = Field(
        default="",
        description="Optimization run UUID or name. If omitted, candidates are returned.",
    )
    trial_id: str = Field(
        default="",
        description="Trial UUID, trial number, 'baseline', or 'best'.",
    )

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
class GetOptimizationTrialTool(BaseTool):
    name = "get_optimization_trial"
    description = (
        "Returns detailed information about a specific optimization trial including "
        "its prompt, score, percentage change from baseline, and creation time."
    )
    category = "optimization"
    input_model = GetOptimizationTrialInput

    def execute(
        self, params: GetOptimizationTrialInput, context: ToolContext
    ) -> ToolResult:

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

        # Calculate percentage change
        pct_change = None
        if (
            trial.average_score is not None
            and run.baseline_score
            and run.baseline_score > 0
        ):
            pct_change = (
                (trial.average_score - run.baseline_score) / run.baseline_score
            ) * 100

        trial_label = "Baseline" if trial.is_baseline else f"Trial {trial.trial_number}"

        info = key_value_block(
            [
                ("Run", run.name),
                ("Trial", trial_label),
                ("Trial ID", f"`{trial.id}`"),
                (
                    "Score",
                    (
                        format_number(trial.average_score)
                        if trial.average_score is not None
                        else "—"
                    ),
                ),
                (
                    "Change vs Baseline",
                    f"{pct_change:+.2f}%" if pct_change is not None else "—",
                ),
                ("Is Baseline", "Yes" if trial.is_baseline else "No"),
                ("Created", format_datetime(trial.created_at)),
            ]
        )

        content = section(f"Trial: {trial_label}", info)

        if trial.prompt:
            content += f"\n\n### Prompt\n\n```\n{truncate(trial.prompt, 1000)}\n```"

        return ToolResult(
            content=content,
            data={
                "trial_id": str(trial.id),
                "trial_number": trial.trial_number,
                "is_baseline": trial.is_baseline,
                "score": (
                    float(trial.average_score)
                    if trial.average_score is not None
                    else None
                ),
                "pct_change": round(pct_change, 2) if pct_change is not None else None,
                "prompt": trial.prompt,
            },
        )
