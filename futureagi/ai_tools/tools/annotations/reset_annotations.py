from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class ResetAnnotationsInput(PydanticBaseModel):
    annotation_id: UUID | None = Field(
        default=None,
        description="The UUID of the annotation task. Omit it to list requirements.",
    )
    row_id: UUID | None = Field(
        default=None,
        description="The UUID of the row to reset annotations for. Omit it to list requirements.",
    )


@register_tool
class ResetAnnotationsTool(BaseTool):
    name = "reset_annotations"
    description = (
        "Resets all annotation values submitted by the current user for a specific row. "
        "Clears the cell values and feedback info, allowing re-annotation."
    )
    category = "annotations"
    input_model = ResetAnnotationsInput

    def execute(
        self, params: ResetAnnotationsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import Annotations
        from model_hub.models.develop_dataset import Cell, Column, Row

        if params.annotation_id is None or params.row_id is None:
            annotations = Annotations.objects.select_related("dataset").order_by(
                "-created_at"
            )[:10]
            rows = [
                f"- `{annotation.id}` — {annotation.name}" for annotation in annotations
            ]
            return ToolResult(
                content=section(
                    "Reset Annotation Requirements",
                    (
                        "This tool only resets the current user's submitted values. "
                        "Provide both `annotation_id` and `row_id`; use `get_annotate_row` "
                        "to find a row before resetting.\n\n"
                        + ("\n".join(rows) if rows else "No annotation tasks found.")
                    ),
                ),
                data={
                    "annotation_id_required": params.annotation_id is None,
                    "row_id_required": params.row_id is None,
                },
            )

        try:
            annotation = Annotations.objects.get(id=params.annotation_id)
        except Annotations.DoesNotExist:
            return ToolResult.not_found("Annotation", str(params.annotation_id))

        # Check user is assigned
        if not annotation.assigned_users.filter(id=context.user.id).exists():
            return ToolResult(
                content=section(
                    "Cannot Reset Annotation",
                    (
                        "You are not assigned to this annotation task. Assign the "
                        "task to the current user first, or choose an annotation task "
                        "assigned to you."
                    ),
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "annotation_name": annotation.name,
                    "requires_assignment": True,
                    "error_code": "PERMISSION_DENIED",
                },
            )

        try:
            row = Row.objects.get(
                id=params.row_id, dataset=annotation.dataset, deleted=False
            )
        except Row.DoesNotExist:
            return ToolResult(
                content=section(
                    "Cannot Reset Annotation Row",
                    (
                        f"Row `{params.row_id}` was not found in annotation "
                        f"`{annotation.name}`. Use `get_annotate_row` with this "
                        "annotation to retrieve a valid row_id before resetting."
                    ),
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "annotation_name": annotation.name,
                    "row_id": str(params.row_id),
                    "requires_valid_row_id": True,
                    "error_code": "NOT_FOUND",
                },
            )

        # Find annotation columns
        ann_cols = Column.objects.filter(
            dataset=annotation.dataset,
            source="annotation_label",
            source_id__startswith=str(annotation.id),
            deleted=False,
        )

        reset_count = 0
        for col in ann_cols:
            cells = Cell.objects.filter(
                row=row, column=col, dataset=annotation.dataset, deleted=False
            )
            for cell in cells:
                if cell.feedback_info and isinstance(cell.feedback_info, dict):
                    ann_info = cell.feedback_info.get("annotation", {})
                    if ann_info.get("user_id") == str(context.user.id):
                        cell.value = ""
                        cell.feedback_info["annotation"] = {
                            "user_id": None,
                            "description": "",
                        }
                        cell.save(
                            update_fields=["value", "feedback_info", "updated_at"]
                        )
                        reset_count += 1

        # Update lowest unfinished row if needed
        if row.order < (annotation.lowest_unfinished_row or float("inf")):
            annotation.lowest_unfinished_row = row.order
            annotation.save(update_fields=["lowest_unfinished_row"])

        info = key_value_block(
            [
                ("Annotation", annotation.name),
                ("Row", f"#{row.order}"),
                ("Cells Reset", str(reset_count)),
            ]
        )

        return ToolResult(
            content=section("Annotations Reset", info),
            data={"reset_count": reset_count, "row_order": row.order},
        )
