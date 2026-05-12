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
from ai_tools.tools.datasets._utils import resolve_dataset_for_tool
from model_hub.constants import MAX_DATASET_NAME_LENGTH


class CloneDatasetInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    new_name: Optional[str] = Field(
        default=None,
        max_length=MAX_DATASET_NAME_LENGTH,
        description="Name for the cloned dataset. Defaults to 'Copy of <original>'.",
    )


@register_tool
class CloneDatasetTool(BaseTool):
    name = "clone_dataset"
    description = (
        "Creates a full copy of a dataset including all columns, rows, and cell values. "
        "The new dataset is independent of the original."
    )
    category = "datasets"
    input_model = CloneDatasetInput

    def execute(self, params: CloneDatasetInput, context: ToolContext) -> ToolResult:
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import clone_dataset as svc_clone

        ds, dataset_result = resolve_dataset_for_tool(
            params.dataset_id,
            context,
            "Dataset Required To Clone",
        )
        if dataset_result:
            return dataset_result

        result = svc_clone(
            source_dataset_id=str(ds.id),
            new_name=params.new_name,
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
        )

        if isinstance(result, ServiceError):
            if "already exists" in result.message.lower():
                from model_hub.models.develop_dataset import Column, Dataset, Row

                existing = Dataset.objects.filter(
                    name=params.new_name or "",
                    organization=context.organization,
                    workspace=context.workspace,
                    deleted=False,
                ).first()
                if existing:
                    row_count = Row.objects.filter(
                        dataset=existing,
                        deleted=False,
                    ).count()
                    column_count = Column.objects.filter(
                        dataset=existing,
                        deleted=False,
                    ).count()
                    info = key_value_block(
                        [
                            ("Dataset ID", f"`{existing.id}`"),
                            ("Name", existing.name),
                            ("Rows", str(row_count)),
                            ("Columns", str(column_count)),
                            (
                                "Link",
                                dashboard_link(
                                    "dataset",
                                    str(existing.id),
                                    label="View Existing Dataset",
                                ),
                            ),
                        ]
                    )
                    return ToolResult(
                        content=section(
                            "Dataset Clone Already Exists",
                            (
                                f"{info}\n\nNo new dataset was created because "
                                "the requested clone name already exists. Use "
                                "this dataset ID, or provide a different "
                                "`new_name` to create another clone."
                            ),
                        ),
                        data={
                            "dataset_id": str(existing.id),
                            "name": existing.name,
                            "rows": row_count,
                            "columns": column_count,
                            "source_dataset_id": str(ds.id),
                            "already_exists": True,
                        },
                    )
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("New Dataset ID", f"`{result['dataset_id']}`"),
                ("Name", result["name"]),
                ("Source", f"Cloned from `{result['source_dataset_id']}`"),
                ("Columns", str(result["columns_cloned"])),
                ("Rows", str(result["rows_cloned"])),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        return ToolResult(
            content=section("Dataset Cloned", info),
            data={
                "dataset_id": result["dataset_id"],
                "name": result["name"],
                "rows": result["rows_cloned"],
                "columns": result["columns_cloned"],
            },
        )
