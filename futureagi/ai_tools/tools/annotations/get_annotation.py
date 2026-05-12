from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, markdown_table, section, truncate
from ai_tools.registry import register_tool


class GetAnnotationInput(PydanticBaseModel):
    annotation_id: str | None = Field(
        default=None,
        description="The name or UUID of the annotation task to retrieve. Omit it to list candidate tasks.",
    )
    annotation_task_id: str | None = Field(
        default=None,
        description="Alias for annotation_id used by some clients.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_annotation_id_aliases(cls, value):
        if isinstance(value, dict):
            data = dict(value)
            if not data.get("annotation_id") and data.get("annotation_task_id"):
                data["annotation_id"] = data.get("annotation_task_id")
            return data
        return value

    @field_validator("annotation_id", mode="before")
    @classmethod
    def blank_annotation_id_means_list_candidates(cls, value):
        if value == "":
            return None
        return value


@register_tool
class GetAnnotationTool(BaseTool):
    name = "get_annotation"
    description = (
        "Returns detailed information about an annotation task including "
        "assigned users, labels, field configuration, and completion progress."
    )
    category = "annotations"
    input_model = GetAnnotationInput

    def execute(self, params: GetAnnotationInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.annotations._utils import candidate_annotations_result
        from model_hub.models.develop_annotations import Annotations

        annotation_ref = (params.annotation_id or "").strip()

        if not annotation_ref:
            annotations = Annotations.objects.select_related("dataset").order_by(
                "-created_at"
            )[:10]
            rows = [
                [
                    f"`{annotation.id}`",
                    annotation.name,
                    annotation.dataset.name if annotation.dataset else "—",
                ]
                for annotation in annotations
            ]
            if not rows:
                return ToolResult(
                    content=section(
                        "Annotation Task Candidates", "No annotation tasks found."
                    ),
                    data={"annotations": []},
                )
            return ToolResult(
                content=section(
                    "Annotation Task Candidates",
                    markdown_table(["ID", "Name", "Dataset"], rows),
                ),
                data={
                    "annotations": [
                        {"id": str(annotation.id), "name": annotation.name}
                        for annotation in annotations
                    ]
                },
            )

        base_qs = Annotations.objects.select_related("dataset").prefetch_related(
            "labels", "assigned_users", "columns"
        )
        try:
            ann = base_qs.get(id=UUID(annotation_ref))
        except (ValueError, TypeError):
            matches = base_qs.filter(name__iexact=annotation_ref)
            if matches.count() == 1:
                ann = matches.first()
            else:
                fuzzy = list(base_qs.filter(name__icontains=annotation_ref)[:5])
                if fuzzy:
                    rows = [
                        [
                            f"`{annotation.id}`",
                            annotation.name,
                            annotation.dataset.name if annotation.dataset else "—",
                        ]
                        for annotation in fuzzy
                    ]
                    return ToolResult(
                        content=section(
                            "Annotation Task Candidates",
                            (
                                f"No exact annotation task matched `{annotation_ref}`. "
                                "Use one of these IDs or exact names:\n\n"
                                + markdown_table(["ID", "Name", "Dataset"], rows)
                            ),
                        ),
                        data={
                            "annotations": [
                                {"id": str(annotation.id), "name": annotation.name}
                                for annotation in fuzzy
                            ],
                            "requires_annotation_id": True,
                        },
                    )
                return ToolResult.not_found("Annotation", annotation_ref)
        except Annotations.DoesNotExist:
            return candidate_annotations_result(
                context,
                "Annotation Task Not Found",
                f"Annotation task `{annotation_ref}` was not found. Use one of these IDs instead.",
            )

        dataset_name = ann.dataset.name if ann.dataset else "—"

        info = key_value_block(
            [
                ("ID", f"`{ann.id}`"),
                ("Name", ann.name),
                ("Dataset", dataset_name),
                ("Responses Required", str(ann.responses or 1)),
                ("Created", format_datetime(ann.created_at)),
            ]
        )

        content = section(f"Annotation: {ann.name}", info)

        # Assigned users
        users = ann.assigned_users.all()
        if users:
            content += "\n\n### Assigned Users\n\n"
            for u in users:
                name = u.name or u.email
                content += f"- {name} (`{u.email}`)\n"
        else:
            content += "\n\n### Assigned Users\n\n_No users assigned._"

        # Labels
        labels = ann.labels.all()
        if labels:
            content += "\n\n### Labels\n\n"
            label_rows = []
            label_data = []
            for lbl in labels:
                settings_info = ""
                if lbl.settings and isinstance(lbl.settings, dict):
                    if lbl.type == "numeric":
                        settings_info = f"min={lbl.settings.get('min', '—')}, max={lbl.settings.get('max', '—')}"
                    elif lbl.type == "categorical":
                        opts = lbl.settings.get("options", [])
                        settings_info = f"{len(opts)} options"
                    elif lbl.type == "star":
                        settings_info = f"{lbl.settings.get('no_of_stars', 5)} stars"

                label_rows.append(
                    [
                        f"`{str(lbl.id)}`",
                        lbl.name,
                        lbl.type or "—",
                        settings_info or "—",
                    ]
                )
                label_data.append(
                    {
                        "id": str(lbl.id),
                        "name": lbl.name,
                        "type": lbl.type,
                        "settings": lbl.settings,
                    }
                )

            content += markdown_table(["ID", "Name", "Type", "Config"], label_rows)
        else:
            content += "\n\n### Labels\n\n_No labels configured._"
            label_data = []

        # Static fields (read-only display)
        if ann.static_fields:
            content += "\n\n### Static Fields (Read-Only)\n\n"
            for sf in ann.static_fields[:10]:
                sf_type = sf.get("type", "—") if isinstance(sf, dict) else "—"
                sf_view = sf.get("view", "—") if isinstance(sf, dict) else "—"
                content += f"- Type: `{sf_type}`, View: `{sf_view}`\n"

        # Response fields (annotable)
        if ann.response_fields:
            content += "\n\n### Response Fields (Annotable)\n\n"
            for rf in ann.response_fields[:10]:
                rf_type = rf.get("type", "—") if isinstance(rf, dict) else "—"
                rf_edit = rf.get("edit", "—") if isinstance(rf, dict) else "—"
                content += f"- Type: `{rf_type}`, Edit: `{rf_edit}`\n"

        # Summary / progress
        summary = ann.summary or {}
        if isinstance(summary, dict) and summary:
            content += "\n\n### Progress\n\n"
            content += f"```json\n{truncate(str(summary), 500)}\n```"

        data = {
            "id": str(ann.id),
            "name": ann.name,
            "dataset": dataset_name,
            "dataset_id": str(ann.dataset_id) if ann.dataset_id else None,
            "responses_required": ann.responses,
            "assigned_users": [{"id": str(u.id), "email": u.email} for u in users],
            "labels": label_data,
        }

        return ToolResult(content=content, data=data)


@register_tool
class GetAnnotationDetailsTool(GetAnnotationTool):
    name = "get_annotation_details"
    description = (
        "Compatibility alias for get_annotation. Returns detailed information "
        "about an annotation task including assigned users, labels, field "
        "configuration, and completion progress."
    )


@register_tool
class GetAnnotationTaskTool(GetAnnotationTool):
    name = "get_annotation_task"
    description = (
        "Compatibility alias for get_annotation. Returns detailed information "
        "about an annotation task using annotation_id or annotation_task_id."
    )
