from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class FilterConfig(PydanticBaseModel):
    column_name: str = Field(description="Name of the column to filter on")
    filter_type: str = Field(
        description="Type of filter: 'text', 'number', 'boolean', 'array'"
    )
    filter_op: str = Field(
        description=(
            "Filter operation. For text: contains, not_contains, equals, not_equals, "
            "starts_with, ends_with. For number: greater_than, less_than, equals, "
            "not_equals, greater_than_or_equal, less_than_or_equal, between, "
            "not_in_between. For boolean: equals."
        )
    )
    filter_value: object = Field(
        description="Value to filter by. For 'between' ops, provide [min, max] array."
    )


class GetDatasetRowsInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max rows to return")
    offset: int = Field(default=0, ge=0, description="Row offset for pagination")
    filters: list[FilterConfig] | None = Field(
        default=None,
        description=(
            "List of column filters. Each filter specifies column_name, "
            "filter_type, filter_op, and filter_value."
        ),
    )
    sort_column: str | None = Field(default=None, description="Column name to sort by")
    sort_direction: str | None = Field(
        default="asc", description="Sort direction: 'asc' or 'desc'"
    )
    search: str | None = Field(
        default=None, description="Search text across all text columns"
    )


@register_tool
class GetDatasetRowsTool(BaseTool):
    name = "get_dataset_rows"
    description = (
        "Returns paginated rows from a dataset with all column values. "
        "Use this to inspect dataset content. Supports pagination, filtering, "
        "sorting, and search for large datasets."
    )
    category = "datasets"
    input_model = GetDatasetRowsInput

    def _dataset_candidates_result(
        self,
        context: ToolContext,
        *,
        title: str = "Dataset Candidates",
        message: str = "",
        unresolved_dataset_id: str = "",
    ) -> ToolResult:
        from model_hub.models.develop_dataset import Dataset, Row

        datasets = Dataset.objects.filter(
            organization=context.organization,
            deleted=False,
        ).order_by("-created_at")[:10]
        rows = [
            [
                f"`{dataset.id}`",
                dataset.name,
                str(Row.objects.filter(dataset=dataset, deleted=False).count()),
            ]
            for dataset in datasets
        ]
        body_parts = []
        if message:
            body_parts.append(message)
        if rows:
            body_parts.append(markdown_table(["ID", "Name", "Rows"], rows))
        else:
            body_parts.append("No datasets found.")
        return ToolResult(
            content=section(title, "\n\n".join(body_parts)),
            data={
                "datasets": [
                    {"id": str(dataset.id), "name": dataset.name}
                    for dataset in datasets
                ],
                "requires_dataset_id": True,
                "unresolved_dataset_id": unresolved_dataset_id,
            },
        )

    def _apply_filters(self, queryset, filters, col_name_to_id):
        """Apply column-based filters to cell queryset, returning filtered row IDs."""
        from model_hub.models.develop_dataset import Cell

        filtered_row_ids = None

        for f in filters:
            col_id = col_name_to_id.get(f.column_name)
            if not col_id:
                continue

            cells_qs = Cell.objects.filter(column_id=col_id, deleted=False)
            ftype = f.filter_type
            fop = f.filter_op
            fval = f.filter_value

            if ftype == "number":
                try:
                    if fop in ("between", "not_in_between"):
                        if not isinstance(fval, list) or len(fval) != 2:
                            continue
                        min_v, max_v = float(fval[0]), float(fval[1])
                        # Filter cells with numeric values in range
                        all_cells = cells_qs.filter(value__regex=r"^-?\d*\.?\d+$")
                        matching = set()
                        for c in all_cells:
                            try:
                                v = float(c.value)
                                if fop == "between" and min_v <= v <= max_v:
                                    matching.add(c.row_id)
                                elif fop == "not_in_between" and not (
                                    min_v <= v <= max_v
                                ):
                                    matching.add(c.row_id)
                            except (ValueError, TypeError):
                                continue
                        row_ids = matching
                    else:
                        fval_num = float(fval)
                        all_cells = cells_qs.filter(value__regex=r"^-?\d*\.?\d+$")
                        matching = set()
                        ops = {
                            "greater_than": lambda v, t: v > t,
                            "less_than": lambda v, t: v < t,
                            "equals": lambda v, t: v == t,
                            "not_equals": lambda v, t: v != t,
                            "greater_than_or_equal": lambda v, t: v >= t,
                            "less_than_or_equal": lambda v, t: v <= t,
                        }
                        op_fn = ops.get(fop)
                        if not op_fn:
                            continue
                        for c in all_cells:
                            try:
                                if op_fn(float(c.value), fval_num):
                                    matching.add(c.row_id)
                            except (ValueError, TypeError):
                                continue
                        row_ids = matching
                except (ValueError, TypeError):
                    continue

            elif ftype in ("text", "array"):
                fval_str = str(fval).lower() if fval else ""
                text_ops = {
                    "contains": {"value__icontains": fval_str},
                    "not_contains": {"value__icontains": fval_str, "_negate": True},
                    "equals": {"value__iexact": fval_str},
                    "not_equals": {"value__iexact": fval_str, "_negate": True},
                    "starts_with": {"value__istartswith": fval_str},
                    "ends_with": {"value__iendswith": fval_str},
                }
                op_config = text_ops.get(fop)
                if not op_config:
                    continue
                negate = op_config.pop("_negate", False)
                matching_cells = cells_qs.filter(**op_config)
                if negate:
                    row_ids = set(
                        cells_qs.exclude(
                            row_id__in=matching_cells.values_list("row_id", flat=True)
                        ).values_list("row_id", flat=True)
                    )
                else:
                    row_ids = set(matching_cells.values_list("row_id", flat=True))

            elif ftype == "boolean":
                fval_str = str(fval).lower()
                if fval_str not in ("true", "false"):
                    continue
                row_ids = set(
                    cells_qs.filter(value__iexact=fval_str).values_list(
                        "row_id", flat=True
                    )
                )
            else:
                continue

            if filtered_row_ids is None:
                filtered_row_ids = row_ids
            else:
                filtered_row_ids = filtered_row_ids & row_ids

        return filtered_row_ids

    def execute(self, params: GetDatasetRowsInput, context: ToolContext) -> ToolResult:

        from model_hub.models.develop_dataset import Cell, Column, Row

        from ai_tools.resolvers import resolve_dataset

        if not params.dataset_id:
            return self._dataset_candidates_result(context)

        dataset, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return self._dataset_candidates_result(
                context,
                title="Dataset Not Found",
                message=(
                    f"{error}\n\nChoose one of these available datasets, or provide "
                    "the exact dataset UUID."
                ),
                unresolved_dataset_id=params.dataset_id,
            )

        # Validate sort_direction
        if params.sort_direction and params.sort_direction not in ("asc", "desc"):
            return ToolResult.error(
                "sort_direction must be 'asc' or 'desc'",
                error_code="VALIDATION_ERROR",
            )

        # Get columns in order (exclude deleted)
        columns = list(
            Column.objects.filter(dataset=dataset, deleted=False).order_by("created_at")
        )
        if not columns:
            return ToolResult(
                content=section(
                    f"Dataset: {dataset.name}", "_Dataset has no columns._"
                ),
                data={"dataset_id": str(dataset.id), "rows": [], "total": 0},
            )

        col_names = [c.name for c in columns]
        col_ids = {str(c.id): c.name for c in columns}
        col_name_to_id = {c.name: str(c.id) for c in columns}

        # Start with base row queryset
        rows_qs = Row.objects.filter(dataset=dataset, deleted=False)

        # Apply filters
        if params.filters:
            filtered_row_ids = self._apply_filters(
                rows_qs, params.filters, col_name_to_id
            )
            if filtered_row_ids is not None:
                rows_qs = rows_qs.filter(id__in=filtered_row_ids)

        # Apply search
        if params.search and params.search.strip():
            search_term = params.search.strip().lower()
            matching_row_ids = set(
                Cell.objects.filter(
                    row__in=rows_qs,
                    deleted=False,
                    value__icontains=search_term,
                ).values_list("row_id", flat=True)
            )
            rows_qs = rows_qs.filter(id__in=matching_row_ids)

        # Apply sort
        if params.sort_column and params.sort_column in col_name_to_id:
            sort_col_id = col_name_to_id[params.sort_column]
            # Sort by cell value in the specified column
            sort_cells = Cell.objects.filter(
                column_id=sort_col_id, deleted=False, row__in=rows_qs
            ).order_by("value" if params.sort_direction == "asc" else "-value")
            sorted_row_ids = list(sort_cells.values_list("row_id", flat=True))
            # Preserve sort order via CASE WHEN
            from django.db.models import Case, IntegerField, When

            ordering = Case(
                *[When(id=rid, then=pos) for pos, rid in enumerate(sorted_row_ids)],
                output_field=IntegerField(),
            )
            rows_qs = rows_qs.filter(id__in=sorted_row_ids).order_by(ordering)
        else:
            rows_qs = rows_qs.order_by("order")

        # Paginate
        total_rows = rows_qs.count()
        rows = list(rows_qs[params.offset : params.offset + params.limit])

        if not rows:
            content = section(
                f"Dataset: {dataset.name}",
                f"Total rows: {total_rows}\n\n_No rows in this range._",
            )
            return ToolResult(
                content=content,
                data={"dataset_id": str(dataset.id), "rows": [], "total": total_rows},
            )

        # Get all cells for these rows (exclude deleted)
        row_ids = [r.id for r in rows]
        cells = Cell.objects.filter(row_id__in=row_ids, deleted=False).select_related(
            "column"
        )

        # Build row data
        cell_map = {}  # {row_id: {col_name: {"value": ..., "column_id": ..., "cell_id": ...}}}
        for cell in cells:
            if cell.row_id not in cell_map:
                cell_map[cell.row_id] = {}
            col_name = (
                cell.column.name
                if cell.column
                else col_ids.get(str(cell.column_id), "?")
            )
            cell_map[cell.row_id][col_name] = {
                "value": cell.value,
                "column_id": str(cell.column_id),
                "cell_id": str(cell.id),
            }

        headers = ["#", "row_id"] + col_names
        table_rows = []
        data_rows = []
        for row in rows:
            row_cells = cell_map.get(row.id, {})
            table_row = [str(row.order), str(row.id)]
            data_row = {"row_id": str(row.id), "order": row.order, "cells": {}}
            for col_name in col_names:
                cell_data = row_cells.get(col_name, {})
                val = cell_data.get("value", "")
                table_row.append(truncate(str(val), 60) if val else "—")
                data_row["cells"][col_name] = {
                    "value": val,
                    "column_id": cell_data.get(
                        "column_id", col_name_to_id.get(col_name, "")
                    ),
                    "cell_id": cell_data.get("cell_id", ""),
                }
            table_rows.append(table_row)
            data_rows.append(data_row)

        table = markdown_table(headers, table_rows)

        # Column reference with IDs
        col_ref_lines = [f"- **{c.name}**: `{str(c.id)}`" for c in columns]
        col_ref = "\n".join(col_ref_lines)

        # Cell reference with IDs (row_id → column_name → cell_id)
        cell_ref_lines = []
        for data_row in data_rows:
            row_id = data_row["row_id"]
            cell_entries = ", ".join(
                f"{col_name}=`{cell_info.get('cell_id', '')}`"
                for col_name, cell_info in data_row["cells"].items()
                if cell_info.get("cell_id")
            )
            if cell_entries:
                cell_ref_lines.append(f"- **{row_id}**: {cell_entries}")
        cell_ref = "\n".join(cell_ref_lines)

        showing = f"Showing rows {params.offset + 1}-{params.offset + len(rows)} of {total_rows}"
        content = section(f"Dataset: {dataset.name}", f"{showing}\n\n{table}")
        content += f"\n\n### Column IDs\n{col_ref}"
        if cell_ref:
            content += f"\n\n### Cell IDs\n{cell_ref}"

        content += f"\n\n{dashboard_link('dataset', str(dataset.id), label='View in Dashboard')}"

        if total_rows > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more rows._"
            )

        return ToolResult(
            content=content,
            data={
                "dataset_id": str(dataset.id),
                "columns": {c.name: str(c.id) for c in columns},
                "rows": data_rows,
                "total": total_rows,
            },
        )
