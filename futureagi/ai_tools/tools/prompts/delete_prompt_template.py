from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import resolve_prompt_template_for_tool


class DeletePromptTemplateInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template to delete. Omit to list candidates.",
    )
    dry_run: bool = Field(
        default=True,
        description="Preview delete impact without modifying data.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Must be true with dry_run=false to perform the soft delete.",
    )


@register_tool
class DeletePromptTemplateTool(BaseTool):
    name = "delete_prompt_template"
    description = (
        "Soft-deletes a prompt template and all its versions. "
        "The template will no longer appear in listings but data is preserved."
    )
    category = "prompts"
    input_model = DeletePromptTemplateInput

    def execute(
        self, params: DeletePromptTemplateInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.run_prompt import PromptTemplate, PromptVersion

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        name = template.name
        version_qs = PromptVersion.objects.filter(original_template=template, deleted=False)
        version_count = version_qs.count()
        eval_config_count = 0
        try:
            from model_hub.models.run_prompt import PromptEvalConfig

            eval_config_count = PromptEvalConfig.objects.filter(
                prompt_template=template,
                deleted=False,
            ).count()
        except Exception:
            eval_config_count = 0

        if params.dry_run or not params.confirm_delete:
            info = key_value_block(
                [
                    ("Template", name),
                    ("Template ID", f"`{template.id}`"),
                    ("Versions Affected", str(version_count)),
                    ("Eval Configs Affected", str(eval_config_count)),
                    (
                        "Required To Delete",
                        "`dry_run=false` and `confirm_delete=true`",
                    ),
                ]
            )
            return ToolResult(
                content=section("Prompt Template Delete Preview", info),
                data={
                    "template_id": str(template.id),
                    "name": name,
                    "versions_affected": version_count,
                    "eval_configs_affected": eval_config_count,
                    "dry_run": True,
                    "requires_confirm_delete": True,
                },
            )

        from django.db import transaction
        from django.utils import timezone

        now = timezone.now()

        with transaction.atomic():
            # Soft delete the template
            template.deleted = True
            template.deleted_at = now
            template.save(update_fields=["deleted", "deleted_at"])

            # Soft delete all versions
            version_count = version_qs.update(deleted=True, deleted_at=now)

            # Soft delete related eval configs
            try:
                from model_hub.models.run_prompt import PromptEvalConfig

                PromptEvalConfig.objects.filter(
                    prompt_template=template, deleted=False
                ).update(deleted=True, deleted_at=now)
            except Exception:
                pass

        return ToolResult(
            content=section(
                "Prompt Template Deleted",
                f"Template **{name}** and {version_count} version(s) have been deleted.",
            ),
            data={
                "id": str(template.id),
                "name": name,
                "versions_deleted": version_count,
            },
        )
