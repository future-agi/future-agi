from typing import Any, Optional

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
    resolve_queue,
    resolve_queue_item,
    uuid_text,
)


class QueueAnnotationValue(PydanticBaseModel):
    label_id: str = Field(description="Annotation label UUID or exact label name")
    value: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Annotation value. Format depends on label type: "
            "TEXT -> {text: 'string'}, NUMERIC -> {value: float}, "
            "STAR -> {rating: float}, CATEGORICAL -> {selected: ['option1']}, "
            "THUMBS_UP_DOWN -> {value: 'up' or 'down'}"
        )
    )


class SubmitQueueAnnotationsInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description="Annotation queue UUID or exact queue name",
    )
    item_id: Optional[str] = Field(
        default="",
        description="Queue item UUID to annotate. Omit to list candidate items.",
    )
    annotations: list[QueueAnnotationValue] = Field(
        default_factory=list,
        description="List of annotation values to submit",
        max_length=100,
    )
    notes: Optional[str] = Field(
        default=None, description="Optional notes for this annotation"
    )


@register_tool
class SubmitQueueAnnotationsTool(BaseTool):
    name = "submit_queue_annotations"
    description = (
        "Submits annotation values for a queue item. Provide the queue ID, item ID, "
        "and annotation values for each label. Automatically completes the item "
        "when the required number of annotations is reached."
    )
    category = "annotations"
    input_model = SubmitQueueAnnotationsInput

    def execute(
        self, params: SubmitQueueAnnotationsInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.annotation_queues import ItemAnnotation

        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved

        item, unresolved = resolve_queue_item(params.item_id, queue)
        if unresolved:
            return unresolved

        if not params.annotations:
            labels = queue.queue_labels.filter(deleted=False).select_related("label")
            label_rows = [
                f"- `{ql.label.id}` - {ql.label.name} ({ql.label.type})"
                for ql in labels
            ]
            return ToolResult(
                content=section(
                    "Queue Annotation Values Required",
                    (
                        "Provide `annotations` with label_id/value pairs for this item.\n\n"
                        + (
                            "\n".join(label_rows)
                            if label_rows
                            else "No labels are attached to this queue."
                        )
                    ),
                ),
                data={
                    "queue_id": str(queue.id),
                    "item_id": str(item.id),
                    "annotations_required": True,
                    "labels": [
                        {
                            "id": str(ql.label.id),
                            "name": ql.label.name,
                            "type": ql.label.type,
                        }
                        for ql in labels
                    ],
                },
            )

        if item.status == "completed":
            return ToolResult(
                content=section(
                    "Queue Item Already Completed",
                    f"Queue item `{item.id}` in `{queue.name}` is already completed.",
                ),
                data={
                    "queue_id": str(queue.id),
                    "item_id": str(item.id),
                    "item_status": item.status,
                    "already_completed": True,
                },
            )

        allowed_labels = [
            queue_label.label
            for queue_label in queue.queue_labels.filter(deleted=False).select_related(
                "label"
            )
            if queue_label.label and not queue_label.label.deleted
        ]
        if not allowed_labels:
            return ToolResult.validation_error(
                "No labels are attached to this queue. Add queue labels before "
                "submitting annotations."
            )

        resolved_annotations = []
        errors = []
        for ann in params.annotations:
            label, error = _resolve_queue_label(ann.label_id, allowed_labels)
            if error:
                errors.append(error)
                continue
            resolved_annotations.append((ann, label))

        if errors:
            allowed_rows = "\n".join(
                f"- `{label.id}` - {label.name} ({label.type})"
                for label in allowed_labels
            )
            return ToolResult.validation_error(
                "Invalid queue label reference(s): "
                + "; ".join(errors)
                + "\n\nUse one of the labels attached to this queue:\n"
                + allowed_rows
            )

        created = 0
        updated = 0

        for ann, label in resolved_annotations:
            # Upsert — update if same (item, user, label) exists
            existing = ItemAnnotation.objects.filter(
                queue_item=item,
                annotator=context.user,
                label=label,
                deleted=False,
            ).first()

            if existing:
                existing.value = ann.value
                if params.notes:
                    existing.notes = params.notes
                existing.save()
                updated += 1
            else:
                ItemAnnotation.objects.create(
                    queue_item=item,
                    annotator=context.user,
                    label=label,
                    value=ann.value,
                    score_source="human",
                    notes=params.notes or "",
                    organization=context.organization,
                    workspace=context.workspace,
                )
                created += 1

        # Update item status
        if item.status == "pending":
            item.status = "in_progress"
            item.save(update_fields=["status", "updated_at"])

        # Auto-complete check: count distinct annotators who submitted
        annotator_count = (
            ItemAnnotation.objects.filter(queue_item=item, deleted=False)
            .values("annotator")
            .distinct()
            .count()
        )
        if annotator_count >= queue.annotations_required:
            item.status = "completed"
            item.save(update_fields=["status", "updated_at"])

        info = key_value_block(
            [
                ("Queue", queue.name),
                ("Item", f"`{item.id}`"),
                ("Created", str(created)),
                ("Updated", str(updated)),
                ("Item Status", item.status),
                ("Errors", str(len(errors)) if errors else "None"),
            ]
        )

        content = section("Queue Annotations Submitted", info)
        if errors:
            content += "\n\n### Errors\n\n" + "\n".join(f"- {e}" for e in errors)

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "item_id": str(item.id),
                "created": created,
                "updated": updated,
                "item_status": item.status,
                "errors": errors,
            },
            is_error=False,
        )


def _resolve_queue_label(
    label_ref: str, allowed_labels: list
) -> tuple[Any | None, str | None]:
    ref = clean_ref(label_ref)
    if not ref:
        return None, "Label reference is required"

    ref_uuid = uuid_text(ref)
    if ref_uuid:
        for label in allowed_labels:
            if str(label.id) == ref_uuid:
                return label, None
        return None, f"Label `{ref}` is not attached to this queue"

    exact = [label for label in allowed_labels if label.name.lower() == ref.lower()]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"Multiple queue labels matched `{ref}`; use a label ID"

    fuzzy = [label for label in allowed_labels if ref.lower() in label.name.lower()]
    if len(fuzzy) == 1:
        return fuzzy[0], None
    if len(fuzzy) > 1:
        return None, f"Multiple queue labels matched `{ref}`; use a label ID"
    return None, f"Label `{ref}` is not attached to this queue"
