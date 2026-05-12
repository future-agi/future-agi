import json

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.datasets._utils import candidate_datasets_result


def _split_row_column_cell_ref(cell_ref: str) -> tuple[str, str] | None:
    parts = str(cell_ref).split("_", 1)
    if len(parts) != 2:
        return None
    row_id, column_id = parts
    from ai_tools.resolvers import is_uuid

    if is_uuid(row_id) and is_uuid(column_id):
        return row_id, column_id
    return None


class UpdateCellValueInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    updates: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of cell_id (UUID string) to new value. "
            "Example: {'cell-uuid-1': 'new value', 'cell-uuid-2': 'another value'}"
        ),
    )


@register_tool
class UpdateCellValueTool(BaseTool):
    name = "update_cell_value"
    description = (
        "Updates individual cell values in a dataset. "
        "Provide a mapping of cell IDs to their new values. "
        "Use get_dataset_rows to find cell IDs first."
    )
    category = "datasets"
    input_model = UpdateCellValueInput

    def execute(self, params: UpdateCellValueInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import is_uuid, resolve_dataset
        from model_hub.models.choices import CellStatus
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.services.dataset_validators import (
            MAX_CELL_VALUE_LENGTH,
            validate_and_convert_cell_value,
            validate_column_is_editable,
        )

        dataset, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return candidate_datasets_result(
                context,
                "Dataset Not Found",
                f"{error} Use one of these dataset IDs or exact names.",
            )

        if not params.updates:
            rows = list(Row.objects.filter(dataset=dataset, deleted=False).order_by("order")[:5])
            columns = list(Column.objects.filter(dataset=dataset, deleted=False).order_by("created_at")[:8])
            sample_rows = []
            if rows and columns:
                sample_cells = Cell.objects.filter(
                    dataset=dataset,
                    row__in=rows,
                    column__in=columns,
                    deleted=False,
                )
                by_pair = {
                    (str(cell.row_id), str(cell.column_id)): cell
                    for cell in sample_cells
                }
                for row in rows:
                    for column in columns:
                        cell = by_pair.get((str(row.id), str(column.id)))
                        if cell:
                            sample_rows.append(
                                [
                                    f"`{cell.id}`",
                                    column.name,
                                    str(cell.value)[:80],
                                ]
                            )
            body = "Provide an `updates` map of cell IDs to new values."
            if sample_rows:
                body += "\n\n" + markdown_table(
                    ["Cell ID", "Column", "Current Value"],
                    sample_rows[:20],
                )
            return ToolResult(
                content=section("Cell Updates Required", body),
                data={
                    "requires_updates": True,
                    "dataset_id": str(dataset.id),
                    "cells": [
                        {"id": row[0].strip("`"), "column": row[1], "value": row[2]}
                        for row in sample_rows[:20]
                    ],
                },
            )

        normalized_updates = {}
        errors = []
        direct_cell_ids = []
        composite_refs = []
        for cell_ref, new_value in params.updates.items():
            cell_ref = str(cell_ref)
            if is_uuid(cell_ref):
                direct_cell_ids.append(cell_ref)
                normalized_updates[cell_ref] = new_value
                continue
            split_ref = _split_row_column_cell_ref(cell_ref)
            if split_ref:
                composite_refs.append((cell_ref, split_ref[0], split_ref[1], new_value))
                continue
            errors.append(
                f"Cell `{cell_ref}` is not a valid cell UUID or row_id_column_id reference"
            )

        # Pre-fetch columns for type validation (avoids N+1)
        cells = Cell.objects.filter(
            id__in=direct_cell_ids, dataset=dataset, deleted=False
        ).select_related("column")
        cell_map = {str(c.id): c for c in cells}

        for original_ref, row_id, column_id, new_value in composite_refs:
            cell = (
                Cell.objects.filter(
                    row_id=row_id,
                    column_id=column_id,
                    dataset=dataset,
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                )
                .select_related("column")
                .first()
            )
            if not cell:
                errors.append(
                    f"Cell `{original_ref}` not found for row `{row_id}` and column `{column_id}`"
                )
                continue
            cell_map[str(cell.id)] = cell
            normalized_updates[str(cell.id)] = new_value

        updated = 0
        for cell_id, new_value in normalized_updates.items():
            cell = cell_map.get(cell_id)
            if not cell:
                errors.append(f"Cell `{cell_id}` not found")
                continue

            # Check if column is editable
            is_editable, edit_err = validate_column_is_editable(cell.column)
            if not is_editable:
                errors.append(f"Cell `{cell_id}`: {edit_err}")
                continue

            # Check max value length
            if isinstance(new_value, str) and len(new_value) > MAX_CELL_VALUE_LENGTH:
                errors.append(
                    f"Cell `{cell_id}`: Value exceeds maximum length of "
                    f"{MAX_CELL_VALUE_LENGTH} characters"
                )
                continue

            data_type = cell.column.data_type if cell.column else "text"
            converted, error = validate_and_convert_cell_value(new_value, data_type)
            if error:
                errors.append(f"Cell `{cell_id}`: {error}")
                continue

            cell.value = converted
            cell.status = CellStatus.PASS.value
            cell.value_infos = json.dumps({})
            cell.save(update_fields=["value", "status", "value_infos", "updated_at"])
            updated += 1

        info = key_value_block(
            [
                ("Dataset", dataset.name),
                ("Cells Updated", str(updated)),
                ("Errors", str(len(errors)) if errors else "None"),
                (
                    "Link",
                    dashboard_link(
                        "dataset", str(dataset.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        title = "Cells Updated" if updated else "Cell Update Not Applied"
        content = section(title, info)
        if errors:
            content += "\n\n### Errors\n\n" + "\n".join(f"- {e}" for e in errors)
            if updated == 0:
                content += (
                    "\n\nUse `get_dataset_rows` to fetch current row, column, and "
                    "cell IDs before retrying the update."
                )

        return ToolResult(
            content=content,
            data={
                "updated": updated,
                "errors": errors,
                "requires_cell_ids": updated == 0 and bool(errors),
            },
            is_error=False,
        )
