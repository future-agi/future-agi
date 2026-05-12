from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import resolve_queue


class DeleteAnnotationQueueInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description="Annotation queue UUID or exact queue name to delete",
    )


@register_tool
class DeleteAnnotationQueueTool(BaseTool):
    name = "delete_annotation_queue"
    description = (
        "Deletes an annotation queue (soft delete). "
        "All items and annotations within the queue are preserved but become inaccessible."
    )
    category = "annotations"
    input_model = DeleteAnnotationQueueInput

    def execute(
        self, params: DeleteAnnotationQueueInput, context: ToolContext
    ) -> ToolResult:
        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved

        queue_name = queue.name
        queue.delete()

        info = key_value_block(
            [
                ("Queue ID", f"`{queue.id}`"),
                ("Name", queue_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Annotation Queue Deleted", info)

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "name": queue_name,
                "deleted": True,
            },
        )
