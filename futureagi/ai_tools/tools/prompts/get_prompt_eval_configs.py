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


class GetPromptEvalConfigsInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Prompt template name or UUID. If omitted, candidate templates are returned.",
    )


@register_tool
class GetPromptEvalConfigsTool(BaseTool):
    name = "get_prompt_eval_configs"
    description = (
        "Returns evaluation configurations set up for a prompt template. "
        "Shows each eval metric, its mapping, model, and status."
    )
    category = "prompts"
    input_model = GetPromptEvalConfigsInput

    def execute(
        self, params: GetPromptEvalConfigsInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_prompt_template
        from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate

        if not params.template_id:
            templates = PromptTemplate.objects.order_by("-updated_at")[:10]
            rows = [
                [
                    f"`{template.id}`",
                    truncate(template.name, 40),
                    format_datetime(template.updated_at),
                ]
                for template in templates
            ]
            return ToolResult(
                content=section(
                    "Prompt Template Candidates",
                    markdown_table(["ID", "Name", "Updated"], rows)
                    if rows
                    else "No prompt templates found.",
                ),
                data={
                    "requires_template_id": True,
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ],
                },
            )

        template_obj, err = resolve_prompt_template(
            params.template_id, context.organization, context.workspace
        )
        if err:
            templates = PromptTemplate.objects.order_by("-updated_at")[:10]
            rows = [
                [
                    f"`{template.id}`",
                    truncate(template.name, 40),
                    format_datetime(template.updated_at),
                ]
                for template in templates
            ]
            return ToolResult(
                content=section(
                    "Prompt Template Not Found",
                    (
                        f"{err}\n\n"
                        + (
                            markdown_table(["ID", "Name", "Updated"], rows)
                            if rows
                            else "No prompt templates found."
                        )
                    ),
                ),
                data={
                    "template_id": params.template_id,
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ],
                },
            )

        try:
            template = PromptTemplate.objects.get(id=template_obj.id)
        except PromptTemplate.DoesNotExist:
            return ToolResult.not_found("Prompt Template", str(template_obj.id))

        configs = PromptEvalConfig.objects.filter(
            prompt_template=template, deleted=False
        ).select_related("eval_template", "eval_group")

        if not configs.exists():
            return ToolResult(
                content=section(
                    f"Eval Configs: {template.name}",
                    "_No evaluation configs found._",
                ),
                data={"configs": []},
            )

        rows = []
        data_list = []
        for cfg in configs:
            eval_name = cfg.eval_template.name if cfg.eval_template else "—"
            group_name = cfg.eval_group.name if cfg.eval_group else "—"
            mapping_str = truncate(str(cfg.mapping), 40) if cfg.mapping else "—"

            model_name = (
                cfg.eval_template.model
                if cfg.eval_template and cfg.eval_template.model
                else "—"
            )

            rows.append(
                [
                    cfg.name or eval_name,
                    eval_name,
                    group_name,
                    model_name,
                    mapping_str,
                    "Active" if not cfg.deleted else "Deleted",
                ]
            )
            data_list.append(
                {
                    "id": str(cfg.id),
                    "name": cfg.name or eval_name,
                    "eval_template_id": (
                        str(cfg.eval_template_id) if cfg.eval_template_id else None
                    ),
                    "eval_template_name": eval_name,
                    "group": group_name,
                    "model": model_name,
                    "mapping": cfg.mapping,
                    "config": cfg.config,
                    "error_localizer": cfg.error_localizer,
                }
            )

        table = markdown_table(
            ["Name", "Template", "Group", "Model", "Mapping", "Status"],
            rows,
        )

        content = section(f"Eval Configs: {template.name} ({len(data_list)})", table)

        return ToolResult(content=content, data={"configs": data_list})
