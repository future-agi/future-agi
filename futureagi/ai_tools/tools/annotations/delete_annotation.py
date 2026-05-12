from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool
from ai_tools.tools.annotations._utils import resolve_annotation


class DeleteAnnotationInput(PydanticBaseModel):
    annotation_ids: Optional[list[str]] = Field(
        default=None,
        description="Annotation task UUIDs or exact names to delete",
        max_length=20,
    )


@register_tool
class DeleteAnnotationTool(BaseTool):
    name = "delete_annotation"
    description = (
        "Deletes annotation tasks and their associated columns and cell values. "
        "This permanently removes the annotation and all its data."
    )
    category = "annotations"
    input_model = DeleteAnnotationInput

    def execute(
        self, params: DeleteAnnotationInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_dataset import Cell, Column

        if not params.annotation_ids:
            _, unresolved = resolve_annotation(
                None,
                context,
                title="Annotation Task Required For Delete",
            )
            return unresolved

        annotations = []
        seen_ids = set()
        for annotation_ref in params.annotation_ids:
            annotation, unresolved = resolve_annotation(
                annotation_ref,
                context,
                title="Annotation Task Required For Delete",
            )
            if unresolved:
                return unresolved
            if annotation.id not in seen_ids:
                annotations.append(annotation)
                seen_ids.add(annotation.id)

        deleted_names = []
        errors = []

        for annotation in annotations:
            ann_name = annotation.name

            # Find and clean up annotation columns
            if annotation.dataset:
                ann_cols = Column.objects.filter(
                    dataset=annotation.dataset,
                    source="annotation_label",
                    source_id__startswith=str(annotation.id),
                    deleted=False,
                )

                for col in ann_cols:
                    Cell.objects.filter(
                        column=col, dataset=annotation.dataset, deleted=False
                    ).update(deleted=True)
                    col.deleted = True
                    col.save(update_fields=["deleted"])

                # Clean column order
                ds = annotation.dataset
                if ds.column_order:
                    col_ids = set(str(c.id) for c in ann_cols)
                    ds.column_order = [c for c in ds.column_order if c not in col_ids]
                    ds.save(update_fields=["column_order"])

            annotation.delete()
            deleted_names.append(ann_name)

        lines = []
        if deleted_names:
            lines.append(f"**Deleted {len(deleted_names)} annotation(s):**")
            for name in deleted_names:
                lines.append(f"- {name}")
        if errors:
            lines.append(f"\n**Errors ({len(errors)}):**")
            for e in errors:
                lines.append(f"- {e}")

        return ToolResult(
            content=section("Annotations Deleted", "\n".join(lines)),
            data={"deleted": deleted_names, "errors": errors},
            is_error=len(deleted_names) == 0,
        )
