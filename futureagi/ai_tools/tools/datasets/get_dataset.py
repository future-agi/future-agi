from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.datasets._utils import candidate_datasets_result

ALLOWED_SOURCES = ("demo", "build", "observe")


class GetDatasetInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    include_sample_rows: bool = Field(
        default=True, description="Include sample rows (up to 5)"
    )


@register_tool
class GetDatasetTool(BaseTool):
    name = "get_dataset"
    description = (
        "Returns detailed information about a specific dataset, including "
        "its schema (columns and types), row count, and sample rows."
    )
    category = "datasets"
    input_model = GetDatasetInput

    def execute(self, params: GetDatasetInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from model_hub.models.develop_dataset import Cell, Column, Row

        if not params.dataset_id:
            from model_hub.models.develop_dataset import Dataset

            datasets = (
                Dataset.objects.filter(
                    organization=context.organization,
                    deleted=False,
                    source__in=ALLOWED_SOURCES,
                )
                .exclude(scenarios__isnull=False)
                .order_by("-created_at")[:10]
            )
            rows = [
                [
                    dataset.name or "Untitled",
                    f"`{dataset.id}`",
                    dataset.source or "—",
                    format_datetime(dataset.created_at),
                ]
                for dataset in datasets
            ]
            if not rows:
                return ToolResult(
                    content="No datasets found in this workspace.",
                    data={"datasets": []},
                )
            return ToolResult(
                content=section(
                    "Candidate Datasets",
                    markdown_table(["Name", "ID", "Source", "Created"], rows),
                ),
                data={
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ]
                },
            )

        ds, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return candidate_datasets_result(
                context,
                "Dataset Not Found",
                f"{error} Use one of these dataset IDs or exact names.",
            )

        if ds.source not in ALLOWED_SOURCES:
            return candidate_datasets_result(
                context,
                "Dataset Not Available",
                f"Dataset `{params.dataset_id}` is not available in this tool. "
                "Use one of these datasets instead.",
            )

        # Get columns ordered by column_order, then any remaining by id
        all_columns = list(Column.objects.filter(dataset=ds, deleted=False))
        col_map = {str(c.id): c for c in all_columns}
        column_order = ds.column_order or []
        column_order_set = set(column_order)
        ordered_cols = []
        for col_id in column_order:
            if col_id in col_map:
                ordered_cols.append(col_map[col_id])
        for c in all_columns:
            if str(c.id) not in column_order_set:
                ordered_cols.append(c)
        col_list = [(c.id, c.name, c.data_type, c.source) for c in ordered_cols]

        # Get row count
        row_count = Row.objects.filter(dataset=ds, deleted=False).count()

        # Basic info
        info = key_value_block(
            [
                ("ID", f"`{ds.id}`"),
                ("Name", ds.name or "Untitled"),
                ("Source", ds.source or "—"),
                ("Model Type", ds.model_type or "—"),
                ("Columns", str(len(col_list))),
                ("Rows", str(row_count)),
                ("Created", format_datetime(ds.created_at)),
                (
                    "Link",
                    dashboard_link("dataset", str(ds.id), label="View in Dashboard"),
                ),
            ]
        )
        content = section(f"Dataset: {ds.name or 'Untitled'}", info)

        # Column schema with IDs for explicit mapping
        if col_list:
            col_rows = []
            for col_id, name, data_type, source in col_list:
                col_rows.append([name, f"`{col_id}`", data_type or "—", source or "—"])
            col_table = markdown_table(["Column", "ID", "Type", "Source"], col_rows)
            content += f"\n\n### Schema\n\n{col_table}"

        # Sample rows
        if params.include_sample_rows and row_count > 0 and col_list:
            sample = self._get_sample_rows(ds, ordered_cols, max_rows=5)
            if sample:
                content += f"\n\n### Sample Rows (up to 5)\n\n{sample}"

        data = {
            "id": str(ds.id),
            "name": ds.name,
            "source": ds.source,
            "model_type": ds.model_type,
            "row_count": row_count,
            "columns": [
                {"id": str(cid), "name": n, "data_type": dt, "source": s}
                for cid, n, dt, s in col_list
            ],
        }

        return ToolResult(content=content, data=data)

    def _get_sample_rows(self, dataset, columns, max_rows: int = 5) -> str:
        from model_hub.models.develop_dataset import Cell, Row

        rows = Row.objects.filter(dataset=dataset, deleted=False).order_by("order")[
            :max_rows
        ]
        if not rows:
            return ""

        col_names = [c.name for c in columns]
        col_ids = {c.id: c.name for c in columns}

        # Fetch cells for these rows
        row_ids = [r.id for r in rows]
        cells = Cell.objects.filter(row_id__in=row_ids, deleted=False)

        # Build a map: row_id -> {col_name -> value}
        cell_map = {}
        for cell in cells:
            row_id = cell.row_id
            col_name = col_ids.get(cell.column_id, "?")
            if row_id not in cell_map:
                cell_map[row_id] = {}
            cell_map[row_id][col_name] = (
                truncate(cell.value, 100) if cell.value else "—"
            )

        # Build table
        table_rows = []
        for row in rows:
            row_data = cell_map.get(row.id, {})
            table_rows.append([row_data.get(cn, "—") for cn in col_names])

        return markdown_table(col_names, table_rows)
