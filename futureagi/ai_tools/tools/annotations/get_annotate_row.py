from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetAnnotateRowInput(PydanticBaseModel):
    annotation_id: Optional[str] = Field(
        default=None,
        description="The UUID or name of the annotation task. Omit it to list candidate annotation tasks.",
    )
    row_order: Optional[int] = Field(
        default=None,
        description="Row order number to annotate (1-indexed). Defaults to lowest unfinished row.",
    )


@register_tool
class GetAnnotateRowTool(BaseTool):
    name = "get_annotate_row"
    description = (
        "Retrieves row data for annotation, including static fields, "
        "response fields, and label details with current values. "
        "Returns the data needed to submit annotations for a specific row."
    )
    category = "annotations"
    input_model = GetAnnotateRowInput

    def execute(self, params: GetAnnotateRowInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.annotations._utils import (
            candidate_annotations_result,
            resolve_annotation,
        )
        from model_hub.models.develop_dataset import Cell, Column, Row

        annotation, annotation_result = resolve_annotation(
            params.annotation_id,
            context,
            title="Annotation Task Required",
        )
        if annotation_result:
            return annotation_result
        annotation = (
            annotation.__class__.objects.prefetch_related("labels", "assigned_users")
            .select_related("dataset")
            .get(id=annotation.id)
        )

        dataset = annotation.dataset
        if not dataset:
            return candidate_annotations_result(
                context,
                "Annotation Has No Dataset",
                (
                    f"Annotation task `{annotation.name}` (`{annotation.id}`) has no "
                    "dataset assigned, so no row can be annotated. Choose a dataset-backed "
                    "annotation task instead."
                ),
            )

        # Get total rows
        total_rows = Row.objects.filter(dataset=dataset, deleted=False).count()
        if total_rows == 0:
            return ToolResult(
                content=section(
                    "Annotation Dataset Has No Rows",
                    (
                        f"Annotation task `{annotation.name}` uses dataset `{dataset.name}`, "
                        "but that dataset has no rows to annotate. Add rows to the dataset "
                        "or choose another annotation task."
                    ),
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "dataset_id": str(dataset.id),
                    "requires_rows": True,
                },
            )

        # Determine which row
        target_order = params.row_order
        if target_order is None:
            target_order = annotation.lowest_unfinished_row or 1

        try:
            row = Row.objects.filter(
                dataset=dataset, order=target_order, deleted=False
            ).first()
            if not row:
                # Try to find nearest row
                row = (
                    Row.objects.filter(dataset=dataset, deleted=False)
                    .order_by("order")
                    .first()
                )
        except Exception:
            return ToolResult(
                content=section(
                    "Annotation Row Not Found",
                    f"Row order `{target_order}` was not found. Try another `row_order`.",
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "requires_row_order": True,
                },
            )

        if not row:
            return ToolResult(
                content=section(
                    "Annotation Row Not Found",
                    "No available rows were found for this annotation task.",
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "requires_row_order": True,
                },
            )

        # Get cells for this row
        cells = Cell.objects.filter(row=row, dataset=dataset, deleted=False)
        cell_map = {}  # column_id -> cell
        for cell in cells:
            cell_map[cell.column_id] = cell

        # Get column info
        col_ids = set(cell_map.keys())
        columns = {
            c.id: c for c in Column.objects.filter(id__in=col_ids, deleted=False)
        }

        # Build static fields
        static_data = []
        if annotation.static_fields:
            for sf in annotation.static_fields:
                col_id_str = sf.get("column_id")
                if col_id_str:
                    try:
                        from uuid import UUID as UUID_

                        col_uuid = UUID_(col_id_str)
                        col = columns.get(col_uuid)
                        cell = cell_map.get(col_uuid)
                        static_data.append(
                            {
                                "column": col.name if col else col_id_str,
                                "value": truncate(cell.value, 200) if cell else "—",
                            }
                        )
                    except ValueError:
                        pass

        # Build label info
        label_data = []
        for label in annotation.labels.all():
            # Find column for this label
            label_cols = Column.objects.filter(
                dataset=dataset,
                source="annotation_label",
                source_id__startswith=f"{annotation.id}-sourceid-{label.id}",
                deleted=False,
            ).order_by("id")

            for col in label_cols:
                cell = cell_map.get(col.id)
                can_annotate = True
                current_value = ""

                if cell and cell.feedback_info:
                    ann_info = cell.feedback_info.get("annotation", {})
                    if ann_info.get("user_id") and ann_info["user_id"] != str(
                        context.user.id
                    ):
                        can_annotate = False
                    elif ann_info.get("user_id") == str(context.user.id):
                        current_value = cell.value or ""

                if can_annotate:
                    label_data.append(
                        {
                            "label_id": str(label.id),
                            "label_name": label.name,
                            "label_type": label.type,
                            "label_settings": label.settings,
                            "column_id": str(col.id),
                            "row_id": str(row.id),
                            "current_value": current_value,
                            "can_annotate": True,
                        }
                    )
                    break  # Only need first available column

        # Navigation
        prev_row = (
            Row.objects.filter(dataset=dataset, order__lt=row.order, deleted=False)
            .order_by("-order")
            .first()
        )
        next_row = (
            Row.objects.filter(dataset=dataset, order__gt=row.order, deleted=False)
            .order_by("order")
            .first()
        )

        # Build content
        info = key_value_block(
            [
                ("Annotation", annotation.name),
                ("Row", f"{row.order} of {total_rows}"),
                ("Row ID", f"`{row.id}`"),
                ("Labels Available", str(len(label_data))),
            ]
        )
        content = section(f"Annotate Row #{row.order}", info)

        # Static fields
        if static_data:
            content += "\n\n### Context Fields\n\n"
            for sf in static_data:
                content += f"**{sf['column']}:** {sf['value']}\n\n"

        # Labels
        if label_data:
            content += "\n\n### Labels to Annotate\n\n"
            rows_table = []
            for ld in label_data:
                settings_str = ""
                if ld["label_type"] == "star":
                    settings_str = (
                        f"1-{ld['label_settings'].get('no_of_stars', 5)} stars"
                    )
                elif ld["label_type"] == "categorical":
                    opts = ld["label_settings"].get("options", [])
                    settings_str = ", ".join(o.get("label", "") for o in opts[:5])
                elif ld["label_type"] == "numeric":
                    settings_str = f"{ld['label_settings'].get('min', 0)}-{ld['label_settings'].get('max', 10)}"
                elif ld["label_type"] == "thumbs_up_down":
                    settings_str = "thumbs_up / thumbs_down"

                rows_table.append(
                    [
                        ld["label_name"],
                        ld["label_type"],
                        settings_str or "—",
                        f"`{ld['column_id']}`",
                        ld["current_value"] or "—",
                    ]
                )

            content += markdown_table(
                ["Label", "Type", "Options", "Column ID", "Current Value"],
                rows_table,
            )

        # Navigation
        nav_parts = []
        if prev_row:
            nav_parts.append(f"Previous: row_order={prev_row.order}")
        if next_row:
            nav_parts.append(f"Next: row_order={next_row.order}")
        if nav_parts:
            content += f"\n\n_Navigation: {' | '.join(nav_parts)}_"

        return ToolResult(
            content=content,
            data={
                "row_id": str(row.id),
                "row_order": row.order,
                "total_rows": total_rows,
                "labels": label_data,
                "static_fields": static_data,
                "prev_order": prev_row.order if prev_row else None,
                "next_order": next_row.order if next_row else None,
            },
        )
