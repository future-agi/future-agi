from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool
from ai_tools.tools.annotations._utils import resolve_label


class DeleteLabelInput(PydanticBaseModel):
    label_id: str | None = Field(
        default=None,
        description="The UUID or exact name of the annotation label to delete",
    )


@register_tool
class DeleteLabelTool(BaseTool):
    name = "delete_annotation_label"
    description = (
        "Deletes an annotation label. "
        "The label must not be in use by any active annotation tasks."
    )
    category = "annotations"
    input_model = DeleteLabelInput

    def execute(self, params: DeleteLabelInput, context: ToolContext) -> ToolResult:

        from model_hub.models.develop_annotations import Annotations
        from model_hub.models.score import Score

        label, unresolved = resolve_label(
            params.label_id,
            context,
            title="Annotation Label Required For Delete",
        )
        if unresolved:
            return unresolved

        # Check if label is in use by active annotation tasks
        if Annotations.objects.filter(labels=label.id, deleted=False).exists():
            return ToolResult.blocked(
                "Cannot delete label: it is in use by active annotation tasks.",
                data={"label_id": str(label.id), "label_name": label.name},
                reason="label_in_use",
            )

        label_name = label.name

        # Soft-delete associated Score records (mirrors backend behavior)
        Score.objects.filter(label_id=label.id).update(deleted=True)

        label.delete()

        return ToolResult(
            content=section(
                "Label Deleted", f"Annotation label **{label_name}** has been deleted."
            ),
            data={"label_name": label_name},
        )
