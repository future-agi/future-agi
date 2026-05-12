from typing import Optional
from uuid import UUID

from django.db.models import Q
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListAnnotationLabelsInput(PydanticBaseModel):
    limit: int = Field(default=50, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    label_type: Optional[str] = Field(
        default=None,
        description="Filter by label type: text, numeric, categorical, star, thumbs_up_down",
    )
    project_id: Optional[UUID] = Field(
        default=None,
        description="Filter labels by project ID",
    )


@register_tool
class ListAnnotationLabelsTool(BaseTool):
    name = "list_annotation_labels"
    description = (
        "Lists available annotation labels in the workspace. "
        "Labels are reusable across annotation tasks and come in 5 types: "
        "text, numeric (with min/max/step), categorical (multiple choice), "
        "star (rating), and thumbs_up_down."
    )
    category = "annotations"
    input_model = ListAnnotationLabelsInput

    def execute(
        self, params: ListAnnotationLabelsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import AnnotationsLabels

        qs = AnnotationsLabels.objects.filter(
            organization=context.organization,
            deleted=False,
        )
        if context.workspace:
            qs = qs.filter(Q(workspace=context.workspace) | Q(workspace__isnull=True))

        if params.project_id:
            qs = qs.filter(project_id=params.project_id)

        if params.label_type:
            qs = qs.filter(type=params.label_type)

        total = qs.count()
        labels = qs.only(
            "id",
            "name",
            "type",
            "settings",
            "description",
            "created_at",
            "project_id",
            "organization_id",
            "workspace_id",
        ).order_by("-created_at")[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for lbl in labels:
            config_summary = "—"
            if lbl.settings and isinstance(lbl.settings, dict):
                if lbl.type == "numeric":
                    mn = lbl.settings.get("min", "—")
                    mx = lbl.settings.get("max", "—")
                    config_summary = f"range: {mn}-{mx}"
                elif lbl.type == "categorical":
                    opts = lbl.settings.get("options", [])
                    config_summary = f"{len(opts)} options"
                elif lbl.type == "star":
                    config_summary = f"{lbl.settings.get('no_of_stars', 5)} stars"
                elif lbl.type == "thumbs_up_down":
                    config_summary = "thumbs"
                elif lbl.type == "text":
                    config_summary = f"max_len={lbl.settings.get('max_length', '—')}"

            rows.append(
                [
                    f"`{lbl.id}`",
                    lbl.name,
                    lbl.type or "—",
                    config_summary,
                    truncate(lbl.description, 40) if lbl.description else "—",
                    format_datetime(lbl.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(lbl.id),
                    "name": lbl.name,
                    "type": lbl.type,
                    "settings": lbl.settings,
                    "description": lbl.description,
                    "project_id": str(lbl.project_id) if lbl.project_id else None,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Type", "Config", "Description", "Created"], rows
        )

        showing = f"Showing {len(rows)} of {total}"
        if params.label_type:
            showing += f" (type: {params.label_type})"

        content = section(f"Annotation Labels ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"labels": data_list, "total": total})
