from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class AddDatasetRowsInput(PydanticBaseModel):
    dataset_id: str = Field(
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    rows: list[dict] = Field(
        description=(
            "Required list of row objects. Do not call this tool without rows. "
            "Each row must be a dict mapping column names to values. "
            "Example: [{'question': 'What is AI?', 'answer': 'AI is...'}]"
        ),
        min_length=1,
        max_length=100,
    )


@register_tool
class AddDatasetRowsTool(BaseTool):
    name = "add_dataset_rows"
    description = (
        "Adds rows to an existing dataset. Each row is a dictionary mapping "
        "column names to cell values. Maximum 100 rows per call. "
        "After create_dataset, use this for actual row values; do not ask "
        "create_dataset to create blank placeholder rows first. Only call this "
        "tool when you can include the rows parameter in the same call."
    )
    category = "datasets"
    input_model = AddDatasetRowsInput

    def execute(self, params: AddDatasetRowsInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import add_dataset_rows as svc_add_rows

        ds, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        result = svc_add_rows(
            dataset_id=str(ds.id),
            rows=params.rows,
            organization=context.organization,
            workspace=context.workspace,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Dataset", result["dataset_name"]),
                ("Dataset ID", f"`{result['dataset_id']}`"),
                ("Rows Added", str(result["rows_added"])),
                ("Cells Created", str(result["cells_created"])),
                ("Total Rows", str(result["total_rows"])),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Rows Added", info)

        return ToolResult(
            content=content,
            data={
                "dataset_id": result["dataset_id"],
                "rows_added": result["rows_added"],
                "cells_created": result["cells_created"],
                "total_rows": result["total_rows"],
            },
        )
