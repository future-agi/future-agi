from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotations._utils import resolve_label


class UpdateLabelInput(PydanticBaseModel):
    label_id: Optional[str] = Field(
        default=None,
        description="The UUID or exact name of the annotation label to update",
    )
    name: Optional[str] = Field(
        default=None,
        description="New name for the label",
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(
        default=None,
        description="New description for the label",
    )
    settings: Optional[dict] = Field(
        default=None,
        description=(
            "Updated label settings. Type-specific: "
            "NUMERIC: {min, max, step_size}, "
            "TEXT: {placeholder, max_length}, "
            "CATEGORICAL: {options: [{label: '...'}], multi_choice: bool}, "
            "STAR: {no_of_stars: int}"
        ),
    )


@register_tool
class UpdateLabelTool(BaseTool):
    name = "update_annotation_label"
    description = "Updates an annotation label's name, description, or settings."
    category = "annotations"
    input_model = UpdateLabelInput

    def execute(self, params: UpdateLabelInput, context: ToolContext) -> ToolResult:

        label, unresolved = resolve_label(
            params.label_id,
            context,
            title="Annotation Label Required For Update",
        )
        if unresolved:
            return unresolved

        changes = []
        if params.name:
            label.name = params.name
            changes.append(f"Name → '{params.name}'")

        if params.description is not None:
            label.description = params.description
            changes.append("Description updated")

        if params.settings is not None:
            label.settings = params.settings
            changes.append("Settings updated")

        label.save()

        info = key_value_block(
            [
                ("Label", label.name),
                ("Type", label.type),
                (
                    "Changes",
                    "\n".join(f"- {c}" for c in changes) if changes else "No changes",
                ),
            ]
        )

        return ToolResult(
            content=section("Label Updated", info),
            data={"label_id": str(label.id), "changes": changes},
        )
