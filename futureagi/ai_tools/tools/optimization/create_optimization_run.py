from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateOptimizationRunInput(PydanticBaseModel):
    name: str = Field(
        description="Name for the optimization run", min_length=1, max_length=255
    )
    column_id: UUID = Field(
        description="The UUID of the dataset column to optimize (the output column)"
    )
    algorithm: str = Field(
        description=(
            "Optimization algorithm: random_search, bayesian, metaprompt, "
            "protegi, promptwizard, gepa"
        )
    )
    algorithm_config: dict = Field(
        description=(
            "Algorithm-specific config. "
            "random_search: {num_variations: 5}. "
            "bayesian: {min_examples: 3, max_examples: 10, n_trials: 5}. "
            "metaprompt: {task_description: '...', num_rounds: 3}. "
            "protegi: {beam_size: 3, num_gradients: 2, errors_per_gradient: 3, "
            "prompts_per_gradient: 2, num_rounds: 3}. "
            "promptwizard: {mutate_rounds: 3, refine_iterations: 2, beam_size: 3}. "
            "gepa: {max_metric_calls: 20}."
        )
    )
    eval_template_ids: Optional[list[UUID]] = Field(
        default=None,
        description="List of UserEvalMetric IDs to use as optimization objectives",
    )
    test_execution_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional UUID of the test execution this optimization run is for "
            "('Fix My Agent'). Links the run to that test execution so it shows "
            "under its Optimization tab. Get it from get_test_execution / "
            "list_test_executions."
        ),
    )
    run_test_id: Optional[UUID] = Field(
        default=None,
        description="Optional UUID of the run test to link this optimization run to.",
    )


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
            ServiceError,
            create_optimization_run,
        )

        result = create_optimization_run(
            name=params.name,
            column_id=params.column_id,
            algorithm=params.algorithm,
            algorithm_config=params.algorithm_config,
            organization=context.organization,
            workspace=context.workspace,
            eval_template_ids=params.eval_template_ids,
            test_execution_id=params.test_execution_id,
            run_test_id=params.run_test_id,
        )

        if isinstance(result, ServiceError):
            if result.code == "NOT_FOUND":
                return ToolResult.not_found("Column", str(params.column_id))
            return ToolResult.error(result.message, error_code=result.code)

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
