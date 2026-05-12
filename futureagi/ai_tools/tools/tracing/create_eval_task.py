from typing import Any, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.tracing._utils import clean_ref, uuid_text


class CreateEvalTaskInput(PydanticBaseModel):
    project_id: str = Field(
        default="",
        description=(
            "Project UUID or exact/fuzzy project name to run evals on. "
            "Omit to list candidate projects."
        ),
    )
    name: str = Field(
        default="",
        description=(
            "Name for this eval task. If omitted and all other required inputs "
            "are present, a sensible default name is generated."
        ),
        max_length=255,
    )
    eval_config_ids: List[str] = Field(
        default_factory=list,
        description=(
            "List of CustomEvalConfig IDs or exact/fuzzy config names to run. "
            "These are eval configs already configured on the project. "
            "Omit to list available configs for the project. Use ['all'] only "
            "when the user asks to run every configured eval."
        ),
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
        "across traces in a project. Call this tool even when IDs are missing; "
        "it will return project or eval-config candidates instead of failing."
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

        requested_config_refs = [clean_ref(eid) for eid in params.eval_config_ids]
        requested_config_refs = [ref for ref in requested_config_refs if ref]
        if not requested_config_refs:
            eval_configs = list(
                CustomEvalConfig.objects.filter(project=project, deleted=False)
                .select_related("eval_template", "eval_group")
                .order_by("-created_at")[:2]
            )
            if len(eval_configs) != 1:
                return _candidate_eval_configs_result(
                    project,
                    "Choose one or more eval configs to include in the eval task.",
                )
        else:
            eval_configs, missing_configs = _resolve_eval_configs(
                requested_config_refs, project
            )
            if missing_configs:
                return _candidate_eval_configs_result(
                    project,
                    "Could not resolve eval config reference(s): "
                    + ", ".join(f"`{ref}`" for ref in missing_configs)
                    + ". Use one of these config IDs.",
                    search=missing_configs[0] if len(missing_configs) == 1 else "",
                )

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

        # Build filters
        filters = params.filters or {}
        filters["project_id"] = str(project.id)

        # Create eval task
        from django.utils import timezone

        task_name = clean_ref(params.name)
        if not task_name:
            task_name = f"{truncate(project.name, 80)} eval task {timezone.now():%Y-%m-%d %H:%M}"

        create_kwargs = {
            "project": project,
            "name": task_name,
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
                "eval_config_ids": [str(config.id) for config in eval_configs],
                "status": eval_task.status,
            },
        )


def _resolve_eval_configs(
    refs: list[Any], project
) -> tuple[list[Any], list[str]]:
    from django.db.models import Q
    from tracer.models.custom_eval_config import CustomEvalConfig

    qs = CustomEvalConfig.objects.filter(project=project, deleted=False).select_related(
        "eval_template"
    )
    if any(clean_ref(ref).lower() in {"*", "all", "all configs", "all eval configs"} for ref in refs):
        return list(qs.order_by("-created_at")[:50]), []

    configs = []
    missing = []
    seen = set()
    for ref_value in refs:
        ref = clean_ref(ref_value)
        if not ref:
            continue

        config = None
        ref_uuid = uuid_text(ref)
        if ref_uuid:
            config = qs.filter(id=ref_uuid).first()
        else:
            exact = list(
                qs.filter(Q(name__iexact=ref) | Q(eval_template__name__iexact=ref))[
                    :2
                ]
            )
            if len(exact) == 1:
                config = exact[0]
            elif len(exact) > 1:
                missing.append(f"{ref} (multiple configs matched; use a config ID)")
                continue
            else:
                fuzzy = list(
                    qs.filter(
                        Q(name__icontains=ref) | Q(eval_template__name__icontains=ref)
                    )[:2]
                )
                if len(fuzzy) == 1:
                    config = fuzzy[0]
                elif len(fuzzy) > 1:
                    missing.append(f"{ref} (multiple configs matched; use a config ID)")
                    continue

        if config is None:
            missing.append(ref)
            continue

        config_id = str(config.id)
        if config_id not in seen:
            configs.append(config)
            seen.add(config_id)

    return configs, missing


def _candidate_eval_configs_result(
    project,
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from django.db.models import Q
    from tracer.models.custom_eval_config import CustomEvalConfig

    qs = CustomEvalConfig.objects.filter(project=project, deleted=False).select_related(
        "eval_template", "eval_group"
    )
    search = clean_ref(search)
    if search and not uuid_text(search):
        qs = qs.filter(Q(name__icontains=search) | Q(eval_template__name__icontains=search))
    configs = list(qs.order_by("-created_at")[:10])

    rows = []
    data = []
    for config in configs:
        template_name = config.eval_template.name if config.eval_template else "-"
        group_name = config.eval_group.name if config.eval_group else "-"
        config_name = config.name or template_name
        rows.append(
            [
                config_name,
                f"`{config.id}`",
                template_name,
                config.model or "-",
                group_name,
                truncate(str(config.mapping), 40) if config.mapping else "-",
            ]
        )
        data.append(
            {
                "id": str(config.id),
                "name": config_name,
                "eval_template_id": str(config.eval_template_id),
                "eval_template_name": template_name,
                "model": config.model,
                "mapping": config.mapping,
            }
        )

    body = detail
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Config ID", "Template", "Model", "Group", "Mapping"],
            rows,
        )
    else:
        body = (
            body + "\n\n" if body else ""
        ) + "No eval configs found on this project. Use `create_custom_eval_config` first."

    return ToolResult.needs_input(
        section(f"Eval Configs: {project.name}", body),
        data={
            "requires_eval_config_ids": True,
            "project_id": str(project.id),
            "project_name": project.name,
            "eval_configs": data,
        },
        missing_fields=["eval_config_ids"],
    )
