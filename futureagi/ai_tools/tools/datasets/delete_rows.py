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


class DeleteRowsInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'",
    )
    row_ids: list[str] = Field(
        default_factory=list,
        description="List of row UUIDs to delete",
        max_length=500,
    )


@register_tool
class DeleteRowsTool(BaseTool):
    name = "delete_rows"
    description = (
        "Soft-deletes rows from a dataset. "
        "The rows are marked as deleted but can potentially be recovered. "
        "Use get_dataset_rows to find row IDs."
    )
    category = "datasets"
    input_model = DeleteRowsInput

    def execute(self, params: DeleteRowsInput, context: ToolContext) -> ToolResult:

        from model_hub.models.develop_dataset import Row

        from ai_tools.resolvers import resolve_dataset
        from ai_tools.tools.datasets._utils import candidate_datasets_result
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import delete_rows as svc_delete_rows

        if not params.dataset_id:
            return candidate_datasets_result(
                context,
                "Dataset Required",
                "Provide `dataset_id` and `row_ids` to soft-delete rows.",
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

        if not params.row_ids:
            rows = list(
                Row.objects.filter(dataset=ds, deleted=False).order_by("id")[:10]
            )
            row_table = (
                markdown_table(
                    ["Row ID"],
                    [[f"`{row.id}`"] for row in rows],
                )
                if rows
                else "No rows found in this dataset."
            )
            return ToolResult(
                content=section(
                    "Row IDs Required",
                    f"Provide `row_ids` to delete rows from `{ds.name}`.\n\n{row_table}",
                ),
                data={
                    "requires_row_ids": True,
                    "dataset_id": str(ds.id),
                    "rows": [{"id": str(row.id)} for row in rows],
                },
            )

        result = svc_delete_rows(
            dataset_id=str(ds.id),
            row_ids=[str(r) for r in params.row_ids],
            organization=context.organization,
        )

        if isinstance(result, ServiceError):
            if result.code == "NOT_FOUND":
                rows = list(
                    Row.objects.filter(dataset=ds, deleted=False).order_by("id")[:10]
                )
                row_table = (
                    markdown_table(["Row ID"], [[f"`{row.id}`"] for row in rows])
                    if rows
                    else "No rows found in this dataset."
                )
                return ToolResult.needs_input(
                    section(
                        "Rows Not Found",
                        (
                            f"{result.message}\n\nUse one of these row IDs from "
                            f"`{ds.name}` or call `get_dataset_rows` before retrying.\n\n"
                            f"{row_table}"
                        ),
                    ),
                    {
                        "requires_row_ids": True,
                        "dataset_id": str(ds.id),
                        "rows": [{"id": str(row.id)} for row in rows],
                        "deleted": 0,
                        "remaining": Row.objects.filter(
                            dataset=ds, deleted=False
                        ).count(),
                    },
                )
            return ToolResult.error(result.message, error_code=result.code)

        remaining = Row.objects.filter(
            dataset_id=result["dataset_id"], deleted=False
        ).count()

        info = key_value_block(
            [
                ("Dataset ID", f"`{result['dataset_id']}`"),
                ("Rows Deleted", str(result["deleted"])),
                ("Remaining Rows", str(remaining)),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        return ToolResult(
            content=section("Rows Deleted", info),
            data={"deleted": result["deleted"], "remaining": remaining},
        )
