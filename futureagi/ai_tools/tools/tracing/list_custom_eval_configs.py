from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListCustomEvalConfigsInput(PydanticBaseModel):
    project_id: str = Field(
        default="",
        description="Project UUID or exact/fuzzy project name.",
    )
    task_id: Optional[UUID] = Field(
        default=None,
        description="Optional eval task ID to filter configs linked to a specific task",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class ListCustomEvalConfigsTool(BaseTool):
    name = "list_custom_eval_configs"
    description = (
        "Lists evaluation configs configured on a tracing project. "
        "These configs define which evals run on spans in the project. "
        "Use the returned IDs when creating eval tasks."
    )
    category = "tracing"
    input_model = ListCustomEvalConfigsInput

    def execute(
        self, params: ListCustomEvalConfigsInput, context: ToolContext
    ) -> ToolResult:

        from tracer.models.custom_eval_config import CustomEvalConfig
        from ai_tools.tools.tracing._utils import resolve_project

        project, unresolved = resolve_project(
            params.project_id,
            context,
            title="Project Required To List Eval Configs",
        )
        if unresolved:
            return unresolved

        qs = CustomEvalConfig.objects.filter(
            project=project, deleted=False
        ).select_related("eval_template", "eval_group")

        if params.task_id:
            qs = qs.filter(eval_tasks__id=params.task_id)

        total = qs.count()
        configs = qs[params.offset : params.offset + params.limit]

        if not configs:
            return ToolResult(
                content=section(
                    f"Eval Configs: {project.name}",
                    "_No eval configs found on this project. Use `create_custom_eval_config` to add one._",
                ),
                data={"configs": [], "total": 0},
            )

        rows = []
        data_list = []
        for cfg in configs:
            template_name = cfg.eval_template.name if cfg.eval_template else "—"
            group_name = cfg.eval_group.name if cfg.eval_group else "—"
            model = cfg.model or "—"
            mapping_str = truncate(str(cfg.mapping), 40) if cfg.mapping else "—"

            rows.append(
                [
                    f"`{cfg.id}`",
                    cfg.name or template_name,
                    template_name,
                    model,
                    group_name,
                    mapping_str,
                    format_datetime(cfg.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(cfg.id),
                    "name": cfg.name or template_name,
                    "eval_template_id": str(cfg.eval_template_id),
                    "eval_template_name": template_name,
                    "model": cfg.model,
                    "mapping": cfg.mapping,
                    "config": cfg.config,
                    "error_localizer": cfg.error_localizer,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Template", "Model", "Group", "Mapping", "Created"],
            rows,
        )

        content = section(
            f"Eval Configs: {project.name} ({total})",
            f"Showing {len(rows)} of {total}\n\n{table}",
        )

        return ToolResult(content=content, data={"configs": data_list, "total": total})
