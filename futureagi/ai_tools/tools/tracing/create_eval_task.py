from typing import List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateEvalTaskInput(PydanticBaseModel):
    project_id: str = Field(
        default="",
        description="Project UUID or exact/fuzzy project name to run evals on.",
    )
    name: str = Field(
        description="Name for this eval task",
        min_length=1,
        max_length=255,
    )
    eval_config_ids: List[str] = Field(
        description=(
            "List of CustomEvalConfig IDs to run. "
            "These are eval configs already configured on the project. "
            "Use list_custom_eval_configs or get_project to find available configs."
        ),
        min_length=1,
    )
    run_type: str = Field(
        default="historical",
        description=(
            "Type of eval run: 'historical' (run on existing spans) "
            "or 'continuous' (run on new incoming spans)"
        ),
    )
    sampling_rate: float = Field(
        default=100.0,
        ge=1.0,
        le=100.0,
        description="Percentage of spans to evaluate (1-100). Default 100%.",
    )
    spans_limit: int = Field(
        default=1000,
        ge=1,
        le=1000000,
        description="Maximum number of spans to evaluate. Default 1000.",
    )
    filters: Optional[dict] = Field(
        default=None,
        description=(
            "Optional filters to narrow which spans to evaluate. "
            "Example: {'span_type': 'llm', 'model': 'gpt-4o'}"
        ),
    )


@register_tool
class CreateEvalTaskTool(BaseTool):
    name = "create_eval_task"
    description = (
        "Creates an eval task to run evaluations on spans in an observe project. "
        "Links existing CustomEvalConfigs to a batch eval job that processes "
        "historical or incoming spans. Use this to evaluate LLM performance "
        "across traces in a project."
    )
    category = "tracing"
    input_model = CreateEvalTaskInput

    def execute(self, params: CreateEvalTaskInput, context: ToolContext) -> ToolResult:

        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.eval_task import (
            EvalTask,
            EvalTaskLogger,
            EvalTaskStatus,
            RunType,
        )
        from ai_tools.tools.tracing._utils import resolve_project

        project, unresolved = resolve_project(
            params.project_id,
            context,
            title="Project Required To Create Eval Task",
        )
        if unresolved:
            return unresolved

        # Validate run_type
        run_type_map = {
            "historical": RunType.HISTORICAL,
            "continuous": RunType.CONTINUOUS,
        }
        if params.run_type not in run_type_map:
            return ToolResult.error(
                f"Invalid run_type '{params.run_type}'. Must be 'historical' or 'continuous'.",
                error_code="VALIDATION_ERROR",
            )

        # Validate eval configs exist and belong to this project
        eval_config_ids = [str(eid) for eid in params.eval_config_ids]
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=eval_config_ids,
            project=project,
            deleted=False,
        )
        found_ids = {str(ec.id) for ec in eval_configs}
        missing_ids = set(eval_config_ids) - found_ids
        if missing_ids:
            return ToolResult.error(
                f"CustomEvalConfig(s) not found on project: {', '.join(missing_ids)}. "
                f"Ensure the eval configs are configured on this project.",
                error_code="NOT_FOUND",
            )

        # Build filters
        filters = params.filters or {}
        filters["project_id"] = str(project.id)

        # Create eval task
        from django.utils import timezone

        create_kwargs = {
            "project": project,
            "name": params.name,
            "filters": filters,
            "sampling_rate": params.sampling_rate,
            "run_type": run_type_map[params.run_type].value,
            "status": EvalTaskStatus.PENDING,
            "last_run": timezone.now(),
        }
        if params.run_type == "historical":
            create_kwargs["spans_limit"] = params.spans_limit

        eval_task = EvalTask.objects.create(**create_kwargs)

        # Link eval configs
        eval_task.evals.set(eval_configs)

        # Create task logger for tracking progress
        EvalTaskLogger.objects.create(
            eval_task=eval_task,
            offset=0,
            status=EvalTaskStatus.PENDING,
        )

        eval_names = [ec.name for ec in eval_configs]

        info = key_value_block(
            [
                ("Eval Task ID", f"`{eval_task.id}`"),
                ("Name", eval_task.name),
                ("Project", project.name),
                ("Run Type", params.run_type),
                ("Evals", ", ".join(eval_names)),
                ("Sampling Rate", f"{params.sampling_rate}%"),
                ("Spans Limit", str(params.spans_limit)),
                ("Status", eval_task.status),
                ("Created", format_datetime(eval_task.created_at)),
            ]
        )

        content = section("Eval Task Created", info)
        content += (
            "\n\n_The eval task is queued and will be picked up by the eval runner. "
            "It will process spans matching the filters and run the configured evals._"
        )

        return ToolResult(
            content=content,
            data={
                "id": str(eval_task.id),
                "name": eval_task.name,
                "project_id": str(project.id),
                "run_type": params.run_type,
                "eval_config_ids": eval_config_ids,
                "status": eval_task.status,
            },
        )
