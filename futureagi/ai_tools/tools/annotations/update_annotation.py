from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import resolve_labels, resolve_users
from ai_tools.tools.annotations._utils import resolve_annotation


class UpdateAnnotationInput(PydanticBaseModel):
    annotation_id: Optional[str] = Field(
        default=None,
        description="The UUID or exact name of the annotation task to update",
    )
    name: Optional[str] = Field(
        default=None,
        description="New name for the annotation task",
        min_length=1,
        max_length=255,
    )
    add_user_ids: Optional[list[str]] = Field(
        default=None,
        description="User UUIDs, emails, or exact names to add as annotators",
    )
    remove_user_ids: Optional[list[str]] = Field(
        default=None,
        description="User UUIDs, emails, or exact names to remove from annotators",
    )
    add_label_ids: Optional[list[str]] = Field(
        default=None,
        description="Annotation label UUIDs or exact names to add to this task",
    )
    remove_label_ids: Optional[list[str]] = Field(
        default=None,
        description="Annotation label UUIDs or exact names to remove from this task",
    )
    responses: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Update number of annotators required per row",
    )


@register_tool
class UpdateAnnotationTool(BaseTool):
    name = "update_annotation"
    description = (
        "Updates an annotation task — add/remove users, add/remove labels, "
        "change name, or update the number of responses required per row."
    )
    category = "annotations"
    input_model = UpdateAnnotationInput

    def execute(
        self, params: UpdateAnnotationInput, context: ToolContext
    ) -> ToolResult:
        from django.utils import timezone

        from model_hub.models.develop_dataset import Cell, Column

        annotation, unresolved = resolve_annotation(
            params.annotation_id,
            context,
            title="Annotation Task Required For Update",
        )
        if unresolved:
            return unresolved

        add_users = []
        remove_users = []
        labels_to_add = []
        labels_to_remove = []

        if params.add_user_ids:
            add_users, missing = resolve_users(params.add_user_ids, context)
            if missing:
                return ToolResult.validation_error(
                    f"User(s) not found: {', '.join(missing)}"
                )

        if params.remove_user_ids:
            remove_users, missing = resolve_users(params.remove_user_ids, context)
            if missing:
                return ToolResult.validation_error(
                    f"User(s) not found: {', '.join(missing)}"
                )

        if params.remove_label_ids:
            labels_to_remove, missing = resolve_labels(params.remove_label_ids, context)
            if missing:
                return ToolResult.validation_error(
                    f"Label(s) not found: {', '.join(missing)}"
                )

        if params.add_label_ids:
            labels_to_add, missing = resolve_labels(params.add_label_ids, context)
            if missing:
                return ToolResult.validation_error(
                    f"Label(s) not found: {', '.join(missing)}"
                )

        changes = []

        if params.name:
            annotation.name = params.name
            changes.append(f"Name updated to '{params.name}'")

        if params.responses is not None:
            annotation.responses = params.responses
            changes.append(f"Responses per row → {params.responses}")

        if params.name or params.responses is not None:
            annotation.save()

        if add_users:
            annotation.assigned_users.add(*add_users)
            changes.append(f"Added {len(add_users)} user(s)")

        if remove_users:
            annotation.assigned_users.remove(*remove_users)
            changes.append(f"Removed {len(remove_users)} user(s)")

        if labels_to_remove:
            now = timezone.now()
            for label in labels_to_remove:
                source_id = f"{annotation.id}-sourceid-{label.id}"
                cols = Column.objects.filter(
                    source_id=source_id,
                    dataset=annotation.dataset,
                    deleted=False,
                )
                col_ids = set(str(c.id) for c in cols)

                # Soft-delete cells and columns
                Cell.objects.filter(
                    dataset=annotation.dataset,
                    column__in=cols,
                    deleted=False,
                ).update(deleted=True, deleted_at=now)
                cols.update(deleted=True, deleted_at=now)

                # Remove from annotation M2M
                annotation.columns.remove(*cols)

                # Clean column order/config
                ds = annotation.dataset
                if ds.column_order:
                    ds.column_order = [c for c in ds.column_order if c not in col_ids]
                if ds.column_config:
                    ds.column_config = {
                        k: v for k, v in ds.column_config.items() if k not in col_ids
                    }
                ds.save(update_fields=["column_order", "column_config"])

            annotation.labels.remove(*labels_to_remove)
            changes.append(f"Removed {len(labels_to_remove)} label(s) + columns")

        if labels_to_add:
            annotation.labels.add(*labels_to_add)

            from model_hub.services.annotation_service import process_annotation_columns

            cols_created = process_annotation_columns(annotation, labels_to_add)
            changes.append(
                f"Added {len(labels_to_add)} label(s) ({cols_created} columns)"
            )

        info = key_value_block(
            [
                ("Annotation", annotation.name),
                ("ID", f"`{annotation.id}`"),
                (
                    "Changes",
                    "\n".join(f"- {c}" for c in changes) if changes else "No changes",
                ),
                ("Users", str(annotation.assigned_users.count())),
                ("Labels", str(annotation.labels.count())),
            ]
        )

        return ToolResult(
            content=section("Annotation Updated", info),
            data={
                "annotation_id": str(annotation.id),
                "changes": changes,
            },
        )
