from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import candidate_queues_result, resolve_queue


class GetQueueProgressInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description=(
            "Annotation queue UUID or exact name. Required to get progress; "
            "omitting it only returns candidate queues and is not a completed progress lookup."
        ),
    )


@register_tool
class GetQueueProgressTool(BaseTool):
    name = "get_queue_progress"
    description = (
        "Gets progress and annotator statistics for an annotation queue. "
        "Shows item status breakdown and per-annotator contribution counts."
    )
    category = "annotations"
    input_model = GetQueueProgressInput

    def execute(
        self, params: GetQueueProgressInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Count, Q

        from model_hub.models.annotation_queues import ItemAnnotation

        if not params.queue_id:
            return candidate_queues_result(
                context,
                "Queue ID Required for Queue Progress",
                "This is a candidate list, not queue progress. Select one queue ID "
                "from the table and call `get_queue_progress` again with `queue_id`.",
            )

        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved

        # Item status breakdown
        item_stats = queue.items.filter(deleted=False).aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status="pending")),
            in_progress=Count("id", filter=Q(status="in_progress")),
            completed=Count("id", filter=Q(status="completed")),
            skipped=Count("id", filter=Q(status="skipped")),
        )

        total = item_stats["total"]
        completed = item_stats["completed"]
        pct = round(completed / total * 100, 1) if total > 0 else 0

        progress_info = key_value_block(
            [
                ("Queue", queue.name),
                ("Status", queue.status),
                ("Total Items", str(total)),
                ("Pending", str(item_stats["pending"])),
                ("In Progress", str(item_stats["in_progress"])),
                ("Completed", str(completed)),
                ("Skipped", str(item_stats["skipped"])),
                ("Overall Progress", f"{pct}%"),
            ]
        )

        content = section("Queue Progress", progress_info)

        # Per-annotator stats
        annotator_stats = (
            ItemAnnotation.objects.filter(queue_item__queue=queue, deleted=False)
            .values("annotator__email", "annotator__id")
            .annotate(annotation_count=Count("id"))
            .order_by("-annotation_count")
        )

        if annotator_stats:
            rows = []
            for stat in annotator_stats:
                rows.append(
                    [
                        stat["annotator__email"] or "—",
                        str(stat["annotation_count"]),
                    ]
                )
            content += "\n\n" + section(
                "Annotator Contributions",
                markdown_table(["Annotator", "Annotations"], rows),
            )

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "total_items": total,
                "completed_items": completed,
                "progress_pct": pct,
                "annotator_stats": [
                    {
                        "email": s["annotator__email"],
                        "count": s["annotation_count"],
                    }
                    for s in annotator_stats
                ],
            },
        )
