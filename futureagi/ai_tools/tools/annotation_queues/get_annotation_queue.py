from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import resolve_queue


class GetAnnotationQueueInput(PydanticBaseModel):
    queue_id: Optional[str] = Field(
        default="",
        description="Annotation queue UUID or exact name. Omit it to list candidate queues.",
    )


@register_tool
class GetAnnotationQueueTool(BaseTool):
    name = "get_annotation_queue"
    description = (
        "Gets detailed information about an annotation queue including its labels, "
        "annotators, item counts, and progress."
    )
    category = "annotations"
    input_model = GetAnnotationQueueInput

    def execute(
        self, params: GetAnnotationQueueInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Count, Q

        from model_hub.models.annotation_queues import AnnotationQueue

        queue, unresolved = resolve_queue(params.queue_id, context)
        if unresolved:
            return unresolved
        queue = (
            AnnotationQueue.objects.select_related(
                "project", "dataset", "agent_definition", "created_by"
            )
            .prefetch_related("queue_labels__label", "queue_annotators__user")
            .get(id=queue.id)
        )

        # Count items
        item_stats = queue.items.filter(deleted=False).aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status="pending")),
            in_progress=Count("id", filter=Q(status="in_progress")),
            completed=Count("id", filter=Q(status="completed")),
            skipped=Count("id", filter=Q(status="skipped")),
        )

        scope = "—"
        if queue.project:
            scope = f"Project: {queue.project.name} (`{queue.project.id}`)"
        elif queue.dataset:
            scope = f"Dataset: {queue.dataset.name} (`{queue.dataset.id}`)"
        elif queue.agent_definition:
            scope = (
                f"Agent: {queue.agent_definition.name} (`{queue.agent_definition.id}`)"
            )

        info = key_value_block(
            [
                ("ID", f"`{queue.id}`"),
                ("Name", queue.name),
                ("Status", queue.status),
                ("Strategy", queue.assignment_strategy),
                ("Annotations Required", str(queue.annotations_required)),
                ("Requires Review", str(queue.requires_review)),
                ("Scope", scope),
                ("Created By", queue.created_by.email if queue.created_by else "—"),
                ("Created", format_datetime(queue.created_at)),
            ]
        )

        if queue.description:
            info += f"\n**Description:** {queue.description}"
        if queue.instructions:
            info += f"\n**Instructions:** {queue.instructions}"

        content = section("Annotation Queue", info)

        # Labels
        queue_labels = queue.queue_labels.filter(deleted=False).select_related("label")
        if queue_labels:
            label_rows = []
            for ql in queue_labels:
                label_rows.append(
                    [
                        f"`{ql.label.id}`",
                        ql.label.name,
                        ql.label.type,
                        "Yes" if ql.required else "No",
                    ]
                )
            content += "\n\n" + section(
                "Labels",
                markdown_table(["ID", "Name", "Type", "Required"], label_rows),
            )

        # Annotators
        queue_annotators = queue.queue_annotators.filter(deleted=False).select_related(
            "user"
        )
        if queue_annotators:
            ann_rows = []
            for qa in queue_annotators:
                ann_rows.append([f"`{qa.user.id}`", qa.user.email, qa.role])
            content += "\n\n" + section(
                "Annotators",
                markdown_table(["ID", "Email", "Role"], ann_rows),
            )

        # Progress
        total = item_stats["total"]
        completed = item_stats["completed"]
        pct = round(completed / total * 100, 1) if total > 0 else 0

        progress = key_value_block(
            [
                ("Total Items", str(total)),
                ("Pending", str(item_stats["pending"])),
                ("In Progress", str(item_stats["in_progress"])),
                ("Completed", str(completed)),
                ("Skipped", str(item_stats["skipped"])),
                ("Progress", f"{pct}%"),
            ]
        )
        content += "\n\n" + section("Progress", progress)

        return ToolResult(
            content=content,
            data={
                "id": str(queue.id),
                "name": queue.name,
                "status": queue.status,
                "item_stats": item_stats,
                "labels": [
                    {"id": str(ql.label.id), "name": ql.label.name}
                    for ql in queue_labels
                ],
                "annotators": [
                    {"id": str(qa.user.id), "email": qa.user.email}
                    for qa in queue_annotators
                ],
            },
        )
