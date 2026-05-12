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


class AddRowsFromExistingInput(PydanticBaseModel):
    target_dataset_id: str = Field(
        default="",
        description="Dataset name or UUID to add rows TO. If omitted, dataset candidates are returned.",
    )
    source_dataset_id: str = Field(
        default="",
        description="Dataset name or UUID to copy rows FROM. If omitted, dataset candidates are returned.",
    )
    column_mapping: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of source column names to target column names. "
            "Example: {'source_input': 'target_input', 'source_output': 'target_output'}"
        ),
    )


@register_tool
class AddRowsFromExistingTool(BaseTool):
    name = "add_rows_from_existing"
    description = (
        "Copies rows from one dataset to another using a column name mapping. "
        "Use list_datasets and get_dataset_rows to find dataset IDs and column names."
    )
    category = "datasets"
    input_model = AddRowsFromExistingInput

    def execute(
        self, params: AddRowsFromExistingInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from model_hub.models.develop_dataset import Column, Dataset, Row
        from model_hub.services.dataset_service import (
            ServiceError,
        )
        from model_hub.services.dataset_service import (
            add_rows_from_existing as svc_add_from_existing,
        )

        if not params.target_dataset_id or not params.source_dataset_id:
            datasets = list(
                Dataset.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{dataset.id}`",
                    dataset.name,
                    str(Row.objects.filter(dataset=dataset, deleted=False).count()),
                ]
                for dataset in datasets
            ]
            return ToolResult(
                content=section(
                    "Add Rows From Existing Requirements",
                    "Provide `source_dataset_id`, `target_dataset_id`, and a column mapping.",
                )
                + "\n\n"
                + (
                    markdown_table(["Dataset ID", "Name", "Rows"], rows)
                    if rows
                    else "No datasets found."
                ),
                data={
                    "requires_source_dataset_id": not bool(params.source_dataset_id),
                    "requires_target_dataset_id": not bool(params.target_dataset_id),
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ],
                },
            )

        source_dataset, source_error = resolve_dataset(
            params.source_dataset_id, context.organization, context.workspace
        )
        if source_error:
            return ToolResult.error(source_error, error_code="NOT_FOUND")
        target_dataset, target_error = resolve_dataset(
            params.target_dataset_id, context.organization, context.workspace
        )
        if target_error:
            return ToolResult.error(target_error, error_code="NOT_FOUND")

        column_mapping = dict(params.column_mapping or {})
        if not column_mapping:
            source_columns = list(
                Column.objects.filter(dataset=source_dataset, deleted=False)
            )
            target_columns = list(
                Column.objects.filter(dataset=target_dataset, deleted=False)
            )
            target_names = {column.name.lower(): column.name for column in target_columns}
            column_mapping = {
                column.name: target_names[column.name.lower()]
                for column in source_columns
                if column.name.lower() in target_names
            }
            if not column_mapping:
                source_rows = [
                    [f"`{column.id}`", column.name, column.data_type]
                    for column in source_columns
                ]
                target_rows = [
                    [f"`{column.id}`", column.name, column.data_type]
                    for column in target_columns
                ]
                content = section(
                    "Column Mapping Required",
                    "No matching column names were found. Provide `column_mapping` from source column names to target column names.",
                )
                content += "\n\n### Source Columns\n"
                content += (
                    markdown_table(["ID", "Name", "Type"], source_rows)
                    if source_rows
                    else "No source columns found."
                )
                content += "\n\n### Target Columns\n"
                content += (
                    markdown_table(["ID", "Name", "Type"], target_rows)
                    if target_rows
                    else "No target columns found."
                )
                return ToolResult(
                    content=content,
                    data={
                        "requires_column_mapping": True,
                        "source_dataset_id": str(source_dataset.id),
                        "target_dataset_id": str(target_dataset.id),
                    },
                )

        result = svc_add_from_existing(
            target_dataset_id=str(target_dataset.id),
            source_dataset_id=str(source_dataset.id),
            column_mapping=column_mapping,
            organization=context.organization,
            workspace=context.workspace,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Target Dataset", result["target_dataset_name"]),
                ("Source Dataset ID", f"`{result['source_dataset_id']}`"),
                ("Rows Added", str(result["rows_added"])),
                ("Columns Mapped", str(result["columns_mapped"])),
                (
                    "Link",
                    dashboard_link(
                        "dataset",
                        result["target_dataset_id"],
                        label="View Target in Dashboard",
                    ),
                ),
            ]
        )

        return ToolResult(
            content=section("Rows Added from Existing Dataset", info),
            data={
                "target_dataset_id": result["target_dataset_id"],
                "rows_added": result["rows_added"],
                "columns_mapped": result["columns_mapped"],
            },
        )
