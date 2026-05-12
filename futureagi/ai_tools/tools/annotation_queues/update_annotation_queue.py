from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import (
    clean_ref,
    resolve_labels,
    resolve_queue,
    resolve_users,
)


class UpdateAnnotationQueueInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description="Annotation queue UUID or exact queue name to update",
    )
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None)
    instructions: Optional[str] = Field(default=None)
    status: Optional[str] = Field(
        default=None,
        description="New status: draft, active, paused, completed",
    )
    annotations_required: Optional[int] = Field(default=None, ge=1, le=10)
    add_label_ids: Optional[list[str]] = Field(
        default=None, description="Label UUIDs or exact names to add to the queue"
    )
    remove_label_ids: Optional[list[str]] = Field(
        default=None, description="Label UUIDs or exact names to remove from the queue"
    )
    add_annotator_ids: Optional[list[str]] = Field(
        default=None, description="User UUIDs, emails, or names to add as annotators"
    )
    remove_annotator_ids: Optional[list[str]] = Field(
        default=None, description="User UUIDs, emails, or names to remove from annotators"
    )


@register_tool
class UpdateAnnotationQueueTool(BaseTool):
    name = "update_annotation_queue"
    description = (
        "Updates an annotation queue's settings, status, labels, or annotators. "
        "Use status transitions: draft->active, active->paused/completed, "
        "paused->active/completed, completed->active."
    )
    category = "annotations"
    input_model = UpdateAnnotationQueueInput

    def execute(
        self, params: UpdateAnnotationQueueInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.annotation_queues import (
            VALID_STATUS_TRANSITIONS,
            AnnotationQueueAnnotator,
            AnnotationQueueLabel,
        )

        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved

        changes = []
        warnings = []

        # Status transition validation
        if params.status:
            status = clean_ref(params.status).lower()
            valid_transitions = VALID_STATUS_TRANSITIONS.get(queue.status, set())
            if status not in valid_transitions:
                warnings.append(
                    f"Status left as `{queue.status}` because `{params.status}` is not a valid transition. "
                    f"Valid transitions: {', '.join(sorted(valid_transitions)) if valid_transitions else 'none'}."
                )
            else:
                queue.status = status
                changes.append(f"Status -> {status}")

        if params.name is not None:
            queue.name = params.name
            changes.append(f"Name -> '{params.name}'")
        if params.description is not None:
            queue.description = params.description
            changes.append("Description updated")
        if params.instructions is not None:
            queue.instructions = params.instructions
            changes.append("Instructions updated")
        if params.annotations_required is not None:
            queue.annotations_required = params.annotations_required
            changes.append(f"Annotations required -> {params.annotations_required}")

        if changes:
            queue.save()

        # Add labels
        if params.add_label_ids:
            labels, missing_labels = resolve_labels(params.add_label_ids, context)
            if missing_labels:
                warnings.append(
                    "Labels not found: " + ", ".join(f"`{ref}`" for ref in missing_labels)
                )
            existing_label_ids = set(
                AnnotationQueueLabel.objects.filter(
                    queue=queue, deleted=False
                ).values_list("label_id", flat=True)
            )
            max_order = (
                AnnotationQueueLabel.objects.filter(queue=queue, deleted=False)
                .order_by("-order")
                .values_list("order", flat=True)
                .first()
                or 0
            )
            added = 0
            for label in labels:
                if label.id not in existing_label_ids:
                    max_order += 1
                    AnnotationQueueLabel.objects.create(
                        queue=queue, label=label, order=max_order
                    )
                    added += 1
            if added:
                changes.append(f"Added {added} label(s)")

        # Remove labels
        if params.remove_label_ids:
            labels, missing_labels = resolve_labels(params.remove_label_ids, context)
            if missing_labels:
                warnings.append(
                    "Labels not found for removal: "
                    + ", ".join(f"`{ref}`" for ref in missing_labels)
                )
            removed = AnnotationQueueLabel.objects.filter(
                queue=queue,
                label_id__in=[label.id for label in labels],
                deleted=False,
            ).update(deleted=True)
            if removed:
                changes.append(f"Removed {removed} label(s)")

        # Add annotators
        if params.add_annotator_ids:
            users, missing_users = resolve_users(params.add_annotator_ids, context)
            if missing_users:
                warnings.append(
                    "Annotators not found: "
                    + ", ".join(f"`{ref}`" for ref in missing_users)
                )
            existing_user_ids = set(
                AnnotationQueueAnnotator.objects.filter(
                    queue=queue, deleted=False
                ).values_list("user_id", flat=True)
            )
            added = 0
            for user in users:
                if user.id not in existing_user_ids:
                    AnnotationQueueAnnotator.objects.create(queue=queue, user=user)
                    added += 1
            if added:
                changes.append(f"Added {added} annotator(s)")

        # Remove annotators
        if params.remove_annotator_ids:
            users, missing_users = resolve_users(params.remove_annotator_ids, context)
            if missing_users:
                warnings.append(
                    "Annotators not found for removal: "
                    + ", ".join(f"`{ref}`" for ref in missing_users)
                )
            removed = AnnotationQueueAnnotator.objects.filter(
                queue=queue,
                user_id__in=[user.id for user in users],
                deleted=False,
            ).update(deleted=True)
            if removed:
                changes.append(f"Removed {removed} annotator(s)")

        if not changes and not warnings:
            return ToolResult(
                content=section(
                    "Annotation Queue Unchanged",
                    "No queue updates were provided. Provide a field to update, or add/remove labels or annotators.",
                ),
                data={"queue_id": str(queue.id), "unchanged": True},
            )

        info = key_value_block(
            [
                ("Queue ID", f"`{queue.id}`"),
                ("Name", queue.name),
                ("Changes", "; ".join(changes) if changes else "None"),
                ("Warnings", "; ".join(warnings) if warnings else "None"),
            ]
        )

        content = section("Annotation Queue Updated", info)

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "changes": changes,
                "warnings": warnings,
            },
        )
