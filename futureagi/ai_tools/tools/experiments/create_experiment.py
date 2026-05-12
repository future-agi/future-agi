from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


def _normalize_prompt_config(prompt_config: list[dict]) -> list[dict]:
    normalized = []
    config_keys = {
        "temperature",
        "frequency_penalty",
        "presence_penalty",
        "max_tokens",
        "top_p",
        "response_format",
        "tool_choice",
        "tools",
    }

    for idx, raw_config in enumerate(prompt_config, start=1):
        config = dict(raw_config)
        model = config.get("model") or config.get("models") or "gpt-4o-mini"
        if isinstance(model, (str, dict)):
            config["model"] = [model]
        else:
            config["model"] = model

        if not config.get("messages"):
            config["messages"] = [{"role": "user", "content": "{{input}}"}]
        if not config.get("name"):
            config["name"] = f"Variant {idx}"

        model_config = dict(config.get("configuration") or {})
        if "maxTokens" in config and "max_tokens" not in model_config:
            model_config["max_tokens"] = config["maxTokens"]
        for key in config_keys:
            if key in config and key not in model_config:
                model_config[key] = config[key]
        config["configuration"] = model_config

        normalized.append(config)

    return normalized


class CreateExperimentInput(PydanticBaseModel):
    name: str = Field(
        description="Name for the experiment", min_length=1, max_length=255
    )
    dataset_id: str = Field(description="Dataset name or UUID to use")
    column_id: str = Field(
        description="Input column name or UUID in the dataset (the column whose values are sent as prompts)"
    )
    prompt_config: list[dict] = Field(
        description=(
            "List of prompt variant configurations. Each must have: "
            "'name' (str), 'messages' (list of {role, content}), "
            "'model' (list of model names like ['gpt-4o']), "
            "'configuration' ({temperature, max_tokens, ...}). "
            "Minimum 2 variants for comparison."
        ),
        min_length=2,
        max_length=10,
    )
    user_eval_template_ids: Optional[list[UUID]] = Field(
        default=None,
        description="Optional list of UserEvalMetric UUIDs to evaluate experiment results",
    )


@register_tool
class CreateExperimentTool(BaseTool):
    name = "create_experiment"
    description = (
        "Creates a new experiment (A/B test) comparing multiple prompt/model variants "
        "on a dataset. Requires a dataset, an input column, and at least 2 prompt configs. "
        "Each prompt config needs: name, messages, model list, and configuration. "
        "A Temporal workflow is started automatically to run the experiment."
    )
    category = "experiments"
    input_model = CreateExperimentInput

    def execute(
        self, params: CreateExperimentInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from ai_tools.tools.datasets.update_column import _resolve_column
        from model_hub.services.experiment_service import (
            ServiceError,
            create_experiment,
        )

        dataset, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        column, error = _resolve_column(dataset, params.column_id)
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        result = create_experiment(
            name=params.name,
            dataset_id=str(dataset.id),
            column_id=str(column.id),
            prompt_config=_normalize_prompt_config(params.prompt_config),
            user=context.user,
            user_eval_template_ids=(
                [str(uid) for uid in params.user_eval_template_ids]
                if params.user_eval_template_ids
                else None
            ),
        )

        if isinstance(result, ServiceError):
            if result.code == "NOT_FOUND":
                return ToolResult.error(result.message, error_code="NOT_FOUND")
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Experiment ID", f"`{result['id']}`"),
                ("Name", result["name"]),
                ("Dataset", result["dataset_name"]),
                ("Input Column", result["column_name"]),
                ("Variants", str(result["variant_count"])),
                ("Status", result["status"]),
                (
                    "Workflow Started",
                    (
                        "Yes"
                        if result["workflow_started"]
                        else "No (will be picked up by periodic task)"
                    ),
                ),
                (
                    "Link",
                    dashboard_link(
                        "experiment", result["id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Experiment Created", info)

        return ToolResult(
            content=content,
            data=result,
        )
