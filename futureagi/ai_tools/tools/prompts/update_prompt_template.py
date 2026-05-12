from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import resolve_prompt_template_for_tool


class UpdatePromptTemplateInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template to update. Omit to list candidates.",
    )
    name: Optional[str] = Field(default=None, description="New name for the template")
    description: Optional[str] = Field(default=None, description="New description")
    folder_id: Optional[str] = Field(
        default=None,
        description="UUID of the folder to move the template to (use empty string to remove from folder)",
    )


@register_tool
class UpdatePromptTemplateTool(BaseTool):
    name = "update_prompt_template"
    description = (
        "Updates a prompt template's metadata (name, description, folder). "
        "Provide only the fields you want to change."
    )
    category = "prompts"
    input_model = UpdatePromptTemplateInput

    def execute(
        self, params: UpdatePromptTemplateInput, context: ToolContext
    ) -> ToolResult:
        from django.utils import timezone

        from model_hub.models.run_prompt import PromptTemplate

        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        update_fields = ["updated_at"]

        if params.name is not None:
            # Check uniqueness
            if (
                PromptTemplate.objects.filter(
                    name=params.name,
                    organization=context.organization,
                    deleted=False,
                )
                .exclude(id=template.id)
                .exists()
            ):
                return ToolResult.error(
                    f"A prompt template named '{params.name}' already exists.",
                    error_code="VALIDATION_ERROR",
                )
            template.name = params.name
            update_fields.append("name")

        if params.description is not None:
            template.description = params.description
            update_fields.append("description")

        if params.folder_id is not None:
            if params.folder_id == "":
                template.prompt_folder = None
            else:
                try:
                    from model_hub.models.prompt_folders import PromptFolder

                    folder = PromptFolder.objects.get(
                        id=params.folder_id,
                        organization=context.organization,
                    )
                    template.prompt_folder = folder
                except Exception:
                    return ToolResult.error(
                        f"Folder with ID '{params.folder_id}' not found.",
                        error_code="NOT_FOUND",
                    )
            update_fields.append("prompt_folder")

        if len(update_fields) <= 1:
            return ToolResult.error(
                "No fields to update. Provide at least one field to change.",
                error_code="VALIDATION_ERROR",
            )

        template.updated_at = timezone.now()
        template.save(update_fields=update_fields)

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Description", template.description or "—"),
                (
                    "Updated Fields",
                    ", ".join(f for f in update_fields if f != "updated_at"),
                ),
            ]
        )

        return ToolResult(
            content=section("Prompt Template Updated", info),
            data={
                "id": str(template.id),
                "name": template.name,
                "updated_fields": [f for f in update_fields if f != "updated_at"],
            },
        )
