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
from ai_tools.tools.datasets._utils import resolve_dataset_for_tool


class DuplicateRowsInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    row_ids: list[str] = Field(
        default_factory=list,
        description="List of row UUIDs to duplicate",
        max_length=500,
    )
    num_copies: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of copies to create for each row (default: 1, max: 100)",
    )


@register_tool
class DuplicateRowsTool(BaseTool):
    name = "duplicate_rows"
    description = (
        "Duplicates specific rows in a dataset, creating copies with identical "
        "cell values. Use get_dataset_rows to find row IDs first."
    )
    category = "datasets"
    input_model = DuplicateRowsInput

    def execute(self, params: DuplicateRowsInput, context: ToolContext) -> ToolResult:
        from model_hub.models.develop_dataset import Row
        from model_hub.services.dataset_service import (
            ServiceError,
        )
        from model_hub.services.dataset_service import (
            duplicate_rows as svc_duplicate_rows,
        )

        ds, dataset_result = resolve_dataset_for_tool(
            params.dataset_id,
            context,
            "Dataset Required To Duplicate Rows",
        )
        if dataset_result:
            return dataset_result

        if not params.row_ids:
            candidates = list(
                Row.objects.filter(dataset=ds, deleted=False).order_by("order")[:20]
            )
            rows = [[str(i), f"`{row.id}`"] for i, row in enumerate(candidates)]
            body = "Provide one or more `row_ids` to duplicate."
            if rows:
                body += "\n\n" + markdown_table(["Index", "Row ID"], rows)
            return ToolResult(
                content=section("Rows Required To Duplicate", body),
                data={
                    "requires_row_ids": True,
                    "dataset_id": str(ds.id),
                    "rows": [{"id": str(row.id)} for row in candidates],
                },
            )

        result = svc_duplicate_rows(
            dataset_id=str(ds.id),
            row_ids=[str(r) for r in params.row_ids],
            num_copies=params.num_copies,
            organization=context.organization,
            workspace=context.workspace,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Dataset", result["dataset_name"]),
                ("Source Rows", str(result["source_rows"])),
                ("Copies per Row", str(result["copies_per_row"])),
                ("Total New Rows", str(result["total_new_rows"])),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        return ToolResult(
            content=section("Rows Duplicated", info),
            data={
                "dataset_id": result["dataset_id"],
                "source_rows": result["source_rows"],
                "total_new_rows": result["total_new_rows"],
            },
        )
