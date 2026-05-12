from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class MergeDatasetsInput(PydanticBaseModel):
    source_dataset_id: str = Field(
        default="",
        description="Dataset name or UUID to merge rows FROM",
    )
    target_dataset_id: str = Field(
        default="",
        description="Dataset name or UUID to merge rows INTO",
    )
    row_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of specific row UUIDs from the source dataset to merge. "
            "If omitted, all rows from the source are merged."
        ),
    )


@register_tool
class MergeDatasetsTool(BaseTool):
    name = "merge_datasets"
    description = (
        "Merges rows from a source dataset into a target dataset. "
        "Columns are matched by name and type; missing columns are created "
        "automatically in the target. Optionally specify which rows to merge."
    )
    category = "datasets"
    input_model = MergeDatasetsInput

    def execute(self, params: MergeDatasetsInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.datasets._utils import (
            candidate_datasets_result,
            resolve_dataset_for_tool,
        )
        from model_hub.services.dataset_service import (
            ServiceError,
        )
        from model_hub.services.dataset_service import merge_datasets as svc_merge

        source_dataset, source_error = resolve_dataset_for_tool(
            params.source_dataset_id,
            context,
            title="Source Dataset Required",
        )
        if source_error:
            return source_error

        target_dataset, target_error = resolve_dataset_for_tool(
            params.target_dataset_id,
            context,
            title="Target Dataset Required",
        )
        if target_error:
            return candidate_datasets_result(
                context,
                "Target Dataset Required",
                (
                    "Provide `target_dataset_id` for the dataset to merge rows into. "
                    f"Source selected: `{source_dataset.name}` (`{source_dataset.id}`)."
                ),
            )

        result = svc_merge(
            source_dataset_id=str(source_dataset.id),
            target_dataset_id=str(target_dataset.id),
            row_ids=[str(r) for r in params.row_ids] if params.row_ids else None,
            organization=context.organization,
            workspace=context.workspace,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Target Dataset", result["target_dataset_name"]),
                ("Source Dataset ID", f"`{result['source_dataset_id']}`"),
                ("Rows Merged", str(result["rows_merged"])),
                ("New Columns Created", str(result["columns_created"])),
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
            content=section("Datasets Merged", info),
            data={
                "target_dataset_id": result["target_dataset_id"],
                "rows_merged": result["rows_merged"],
                "columns_created": result["columns_created"],
            },
        )
