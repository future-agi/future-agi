from typing import Any, Literal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from pydantic import model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool

DataTypeLiteral = Literal[
    "text",
    "boolean",
    "integer",
    "float",
    "json",
    "array",
    "image",
    "images",
    "datetime",
    "audio",
    "document",
]


class ColumnDef(PydanticBaseModel):
    name: str = Field(description="Column name", min_length=1, max_length=255)
    data_type: DataTypeLiteral = Field(
        default="text",
        description=(
            "Data type: text, integer, float, boolean, json, "
            "array, image, images, datetime, audio, document"
        ),
    )


class AddColumnsInput(PydanticBaseModel):
    dataset_id: str = Field(
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    columns: list[ColumnDef] = Field(
        description="List of columns to add with name and data_type",
        min_length=1,
        max_length=20,
    )
    column_types: list[DataTypeLiteral] | None = Field(
        default=None,
        description=(
            "Optional legacy parallel list of data types. Prefer putting "
            "data_type on each column object."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_column_inputs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        columns = data.get("columns")
        column_types = data.get("column_types") or data.get("types")

        if isinstance(columns, str):
            columns = [part.strip() for part in columns.split(",") if part.strip()]

        if isinstance(column_types, str):
            column_types = [
                part.strip() for part in column_types.split(",") if part.strip()
            ]

        def type_at(index: int) -> str:
            if isinstance(column_types, list) and index < len(column_types):
                return column_types[index]
            return "text"

        if isinstance(columns, list):
            normalized = []
            changed = False
            for index, column in enumerate(columns):
                if isinstance(column, str):
                    normalized.append({"name": column, "data_type": type_at(index)})
                    changed = True
                    continue
                if isinstance(column, dict):
                    column_data = dict(column)
                    if "data_type" not in column_data:
                        column_data["data_type"] = (
                            column_data.pop("column_type", None)
                            or column_data.pop("type", None)
                            or type_at(index)
                        )
                        changed = True
                    normalized.append(column_data)
                    continue
                normalized.append(column)
            if changed:
                data["columns"] = normalized
                if isinstance(column_types, list):
                    data["column_types"] = column_types

        return data


@register_tool
class AddColumnsTool(BaseTool):
    name = "add_columns"
    description = (
        "Adds new columns to an existing dataset. "
        "Existing rows will have empty cells for the new columns."
    )
    category = "datasets"
    input_model = AddColumnsInput

    def execute(self, params: AddColumnsInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import add_columns as svc_add_columns

        # data_type is already validated by the Literal type in ColumnDef.

        ds, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        columns_data = [
            {"name": c.name, "data_type": c.data_type} for c in params.columns
        ]

        result = svc_add_columns(
            dataset_id=str(ds.id),
            columns_data=columns_data,
            organization=context.organization,
        )

        if isinstance(result, ServiceError):
            if "already exist" in result.message.lower():
                from model_hub.models.develop_dataset import Column

                requested_names = [column["name"] for column in columns_data]
                existing = Column.objects.filter(
                    dataset=ds,
                    name__in=requested_names,
                    deleted=False,
                ).order_by("name")
                rows = [
                    [column.name, column.data_type, f"`{column.id}`"]
                    for column in existing
                ]
                return ToolResult(
                    content=section(
                        "Columns Already Exist",
                        (
                            f"{result.message}\n\n"
                            "Use new column names, or continue with these existing columns.\n\n"
                            + (
                                markdown_table(["Name", "Type", "ID"], rows)
                                if rows
                                else ""
                            )
                        ),
                    ),
                    data={
                        "dataset_id": str(ds.id),
                        "already_exists": True,
                        "existing_columns": [
                            {
                                "id": str(column.id),
                                "name": column.name,
                                "data_type": column.data_type,
                            }
                            for column in existing
                        ],
                    },
                )
            return ToolResult.error(result.message, error_code=result.code)

        rows_table = [
            [c["name"], c["data_type"], f"`{c['id']}`"] for c in result["columns"]
        ]
        table = markdown_table(["Name", "Type", "ID"], rows_table)

        info = key_value_block(
            [
                ("Dataset ID", f"`{result['dataset_id']}`"),
                ("Columns Added", str(result["columns_added"])),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Columns Added", info)
        content += f"\n\n### New Columns\n\n{table}"

        return ToolResult(
            content=content,
            data={"columns": result["columns"]},
        )
