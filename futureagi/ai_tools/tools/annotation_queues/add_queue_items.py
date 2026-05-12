from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import resolve_queue, uuid_text

VALID_SOURCE_TYPES = {
    "dataset_row",
    "trace",
    "observation_span",
    "prototype_run",
    "call_execution",
    "trace_session",
}


class QueueItemInput(PydanticBaseModel):
    source_type: str = Field(
        description=(
            "Type of item: dataset_row, trace, observation_span, "
            "prototype_run, call_execution, trace_session"
        )
    )
    source_id: str = Field(description="UUID of the source object")


class AddQueueItemsInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description="Annotation queue UUID or exact name. Omit it to list candidate queues.",
    )
    items: list[QueueItemInput] = Field(
        default_factory=list,
        description="List of items to add to the queue",
        max_length=500,
    )


@register_tool
class AddQueueItemsTool(BaseTool):
    name = "add_queue_items"
    description = (
        "Adds items to an annotation queue. Items can be dataset rows, traces, "
        "observation spans, call executions (simulations), prototype runs, or trace sessions. "
        "Duplicate items (same source in same queue) are skipped."
    )
    category = "annotations"
    input_model = AddQueueItemsInput

    def execute(self, params: AddQueueItemsInput, context: ToolContext) -> ToolResult:
        from model_hub.models.annotation_queues import (
            SOURCE_TYPE_FK_MAP,
            QueueItem,
        )

        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved

        if not params.items:
            return ToolResult(
                content=section(
                    "Queue Items Required",
                    "Provide `items` with `source_type` and `source_id`. Use list/read tools first to identify candidate source IDs.",
                ),
                data={"queue_id": str(queue.id), "items_required": True},
            )

        added = 0
        skipped = 0
        errors = []

        # Get current max order
        max_order = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
            or 0
        )

        for item in params.items:
            source_type = item.source_type.strip().lower().replace("-", "_").replace(" ", "_")
            if source_type not in VALID_SOURCE_TYPES:
                errors.append(
                    f"Invalid source_type '{item.source_type}' for {item.source_id}"
                )
                continue
            source_id = uuid_text(item.source_id)
            if not source_id:
                errors.append(
                    f"Invalid source_id `{item.source_id}` for {source_type}; provide a UUID from a list/get tool."
                )
                continue

            fk_field = SOURCE_TYPE_FK_MAP.get(source_type)
            if not fk_field:
                errors.append(f"Unknown source_type: {source_type}")
                continue

            # Check for duplicate
            existing = QueueItem.objects.filter(
                queue=queue,
                source_type=source_type,
                deleted=False,
                **{f"{fk_field}_id": source_id},
            ).exists()
            if existing:
                skipped += 1
                continue

            max_order += 1
            try:
                QueueItem.objects.create(
                    queue=queue,
                    source_type=source_type,
                    order=max_order,
                    organization=context.organization,
                    workspace=context.workspace,
                    **{f"{fk_field}_id": source_id},
                )
                added += 1
            except Exception as e:
                errors.append(f"{source_type} {item.source_id}: {e}")

        info = key_value_block(
            [
                ("Queue", queue.name),
                ("Added", str(added)),
                ("Skipped (duplicates)", str(skipped)),
                ("Errors", str(len(errors)) if errors else "None"),
            ]
        )

        content = section("Items Added to Queue", info)
        if errors:
            content += "\n\n### Errors\n\n" + "\n".join(f"- {e}" for e in errors[:20])

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "added": added,
                "skipped": skipped,
                "errors": errors,
            },
            is_error=False,
        )
