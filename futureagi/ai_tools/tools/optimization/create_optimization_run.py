from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid
from ai_tools.tools.optimization._utils import candidate_columns_result, resolve_column


def _default_algorithm_config(algorithm: str) -> dict:
    defaults = {
        "random_search": {"num_variations": 5},
        "bayesian": {"min_examples": 3, "max_examples": 10, "n_trials": 5},
        "metaprompt": {"task_description": "Improve this prompt.", "num_rounds": 3},
        "protegi": {
            "beam_size": 3,
            "num_gradients": 2,
            "errors_per_gradient": 3,
            "prompts_per_gradient": 2,
            "num_rounds": 3,
        },
        "promptwizard": {"mutate_rounds": 3, "refine_iterations": 2, "beam_size": 3},
        "gepa": {"max_metric_calls": 20},
    }
    return defaults.get(algorithm, defaults["random_search"])


class CreateOptimizationRunInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(
        default="",
        description="Name for the optimization run",
        max_length=255,
    )
    column_id: str = Field(
        default="",
        description="The UUID or exact name of the dataset column to optimize",
    )
    algorithm: str = Field(
        default="random_search",
        description=(
            "Optimization algorithm: random_search, bayesian, metaprompt, "
            "protegi, promptwizard, gepa"
        ),
    )
    algorithm_config: dict | None = Field(
        default=None,
        description=(
            "Algorithm-specific config. "
            "random_search: {num_variations: 5}. "
            "bayesian: {min_examples: 3, max_examples: 10, n_trials: 5}. "
            "metaprompt: {task_description: '...', num_rounds: 3}. "
            "protegi: {beam_size: 3, num_gradients: 2, errors_per_gradient: 3, "
            "prompts_per_gradient: 2, num_rounds: 3}. "
            "promptwizard: {mutate_rounds: 3, refine_iterations: 2, beam_size: 3}. "
            "gepa: {max_metric_calls: 20}."
        ),
    )
    eval_template_ids: list[str] | None = Field(
        default=None,
        description="List of UserEvalMetric IDs to use as optimization objectives",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["name"] = (
            normalized.get("name")
            or normalized.get("optimization_name")
            or normalized.get("run_name")
            or ""
        )
        normalized["column_id"] = (
            normalized.get("column_id")
            or normalized.get("output_column_id")
            or normalized.get("target_column_id")
            or normalized.get("column")
            or ""
        )
        algorithm = str(normalized.get("algorithm") or "random_search").strip().lower()
        normalized["algorithm"] = algorithm
        if normalized.get("algorithm_config") is None:
            normalized["algorithm_config"] = _default_algorithm_config(algorithm)
        return normalized


@register_tool
class CreateOptimizationRunTool(BaseTool):
    name = "create_optimization_run"
    description = (
        "Creates a new prompt optimization run. Tests different prompt variants "
        "to find the best-performing one using the specified algorithm. "
        "Requires a dataset column and algorithm configuration."
    )
    category = "optimization"
    input_model = CreateOptimizationRunInput

    def execute(
        self, params: CreateOptimizationRunInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.services.optimization_service import (
            VALID_ALGORITHMS,
            ServiceError,
            create_optimization_run,
        )

        name = (params.name or "").strip()
        if not name:
            name = "Falcon optimization run"

        algorithm = (params.algorithm or "random_search").strip().lower()
        if algorithm not in VALID_ALGORITHMS:
            return ToolResult(
                content=section(
                    "Optimization Algorithm Required",
                    (
                        f"`{algorithm}` is not supported. Use one of: "
                        + ", ".join(f"`{a}`" for a in sorted(VALID_ALGORITHMS))
                    ),
                ),
                data={
                    "requires_algorithm": True,
                    "valid_algorithms": sorted(VALID_ALGORITHMS),
                },
            )

        column, unresolved = resolve_column(params.column_id, context)
        if unresolved:
            return unresolved

        eval_template_ids = [
            ref for ref in (params.eval_template_ids or []) if is_uuid(str(ref))
        ]

        result = create_optimization_run(
            name=name,
            column_id=column.id,
            algorithm=algorithm,
            algorithm_config=params.algorithm_config
            or _default_algorithm_config(algorithm),
            organization=context.organization,
            workspace=context.workspace,
            eval_template_ids=eval_template_ids or None,
        )

        if isinstance(result, ServiceError):
            if result.code == "NOT_FOUND":
                return candidate_columns_result(
                    context,
                    "Dataset Column Not Found",
                    result.message,
                    search=params.column_id,
                )
            return ToolResult(
                content=section("Optimization Run Requirements", result.message),
                data={"error_code": result.code},
            )

        info = key_value_block(
            [
                ("ID", f"`{result['optimization_id']}`"),
                ("Name", result["name"]),
                ("Algorithm", result["algorithm"]),
                ("Dataset", result["dataset_name"]),
                ("Column", result["column"].name),
                ("Status", result["status"]),
            ]
        )

        content = section("Optimization Run Created", info)
        if result["workflow_started"]:
            content += "\n\n_The optimization run has started. Use `get_optimization_run` to track progress._"
        else:
            content += "\n\n_Warning: Failed to start the optimization workflow. The run has been marked as failed._"

        return ToolResult(
            content=content,
            data={
                "optimization_id": result["optimization_id"],
                "name": result["name"],
                "algorithm": result["algorithm"],
                "status": result["status"],
            },
        )
