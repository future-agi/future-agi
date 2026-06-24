from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool


class DeleteLabelInput(PydanticBaseModel):
    label_id: UUID = Field(description="The UUID of the annotation label to delete")


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

        from model_hub.models.develop_annotations import (
            Annotations,
            AnnotationsLabels,
        )
        from model_hub.models.score import Score

        try:
            label = AnnotationsLabels.objects.get(
                id=params.label_id,
                organization=context.organization,
            )
        except AnnotationsLabels.DoesNotExist:
            return ToolResult.not_found("AnnotationLabel", str(params.label_id))

        # Check if label is in use by active annotation tasks
        if Annotations.objects.filter(labels=params.label_id, deleted=False).exists():
            return ToolResult.error(
                "Cannot delete label: it is in use by active annotation tasks.",
                error_code="LABEL_IN_USE",
            )

        label_name = label.name

        # Soft-delete associated Score records (mirrors backend behavior) and
        # mirror the deletion to CH: the Score->CH mirror is wired on post_save,
        # which a queryset .update() bypasses, so CDC-off the deleted rows would
        # otherwise stay live in CH and the annotation filters would keep
        # returning them.
        from tracer.services.clickhouse.v2.score_writer import soft_delete_scores

        soft_delete_scores(Score.objects.filter(label_id=params.label_id))

        label.delete()

        return ToolResult(
            content=section(
                "Label Deleted", f"Annotation label **{label_name}** has been deleted."
            ),
            data={"label_name": label_name},
        )
