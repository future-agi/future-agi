from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class CreateEvalTaskInput(PydanticBaseModel):
    project_id: UUID = Field(description="The UUID of the project to run evals on")
    name: str = Field(
        description="Name for this eval task",
        min_length=1,
        max_length=255,
    )
    eval_config_ids: list[UUID] = Field(
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
    filters: dict | None = Field(
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

        from django.db import transaction

        from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.eval_task import (
            EvalTask,
            EvalTaskLogger,
            EvalTaskStatus,
            RunType,
        )
        from tracer.models.project import Project

        # Validate project
        try:
            project = Project.objects.get(
                id=params.project_id, organization=context.organization
            )
        except Project.DoesNotExist:
            return ToolResult.not_found("Project", str(params.project_id))

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
        filters["project_id"] = str(params.project_id)

        # Last gate before a paid run: does each config's mapping actually resolve
        # on the spans this task will read? `create_custom_eval_config` checks that
        # a mapping value is a known attribute NAME, and the project attribute list
        # is a union over every span, so a path that only ever appears on one span
        # type passes that check and then reads empty on the type this task is
        # scoped to. Nothing downstream catches it: the run just errors every row.
        blocked = self._configs_that_resolve_on_nothing(project, filters, eval_configs)
        if blocked:
            return ToolResult.error(
                "These eval configs map to attributes that carry no value on the "
                "spans this task targets, so every row would error:\n"
                + "\n".join(blocked)
                + "\n\nRead a target span with `get_span` or `read_trace_span` and "
                "map to a path you have seen hold content on that span.",
                error_code="VALIDATION_ERROR",
            )

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

        with transaction.atomic():
            eval_task = EvalTask.objects.create(**create_kwargs)

            # Link eval configs
            eval_task.evals.set(eval_configs)

            # Create task logger for tracking progress
            EvalTaskLogger.objects.create(
                eval_task=eval_task,
                offset=0,
                status=EvalTaskStatus.PENDING,
            )
            transaction.on_commit(lambda: start_eval_task_workflow_sync(eval_task))

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

    _MAPPING_SAMPLE = 5

    def _configs_that_resolve_on_nothing(self, project, filters, eval_configs):
        """One line per config whose mapping resolves on none of a small sample.

        Sampled rather than exhaustive: a mapping that reads empty on five spans
        of the targeted type reads empty on all of them, and a sample keeps this
        to one query on the create path.
        """
        from tracer.models.observation_span import ObservationSpan
        from tracer.utils.eval import mapping_resolution_coverage

        obs_types = filters.get("observation_type")
        scope = "spans"
        try:
            span_qs = ObservationSpan.objects.filter(project=project)
            if obs_types:
                span_qs = span_qs.filter(observation_type__in=obs_types)
                scope = f"`{'`, `'.join(str(t) for t in obs_types)}` spans"
            sample = list(span_qs.order_by("-start_time")[: self._MAPPING_SAMPLE])
        except Exception as e:
            # Advisory read. A project with nothing to sample already gets no
            # opinion, and a sample that cannot be taken is the same condition;
            # neither is a reason to refuse a create the caller is entitled to.
            logger.warning("eval_task_mapping_sample_unavailable", error=str(e))
            return []
        if not sample:
            return []

        blocked = []
        for config in eval_configs:
            if not config.mapping:
                continue
            hits = mapping_resolution_coverage(
                config.mapping, sample, config.eval_template_id
            )
            if hits == 0:
                pairs = ", ".join(f"`{k}` -> `{v}`" for k, v in config.mapping.items())
                blocked.append(
                    f"- `{config.name}`: {pairs} resolves on 0 of "
                    f"{len(sample)} sampled {scope}"
                )
        return blocked
