from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from model_hub.constants import (
    MAX_DATASET_NAME_LENGTH,
    MAX_MANUAL_COLUMNS,
    MAX_MANUAL_ROWS,
)


class CreateDatasetInput(PydanticBaseModel):
    name: str = Field(
        default="",
        description="Name for the new dataset",
        max_length=MAX_DATASET_NAME_LENGTH,
    )
    columns: list[str] = Field(
        default_factory=list,
        description="List of column names to create (e.g. ['input', 'expected_output', 'context'])",
        max_length=MAX_MANUAL_COLUMNS,
    )
    column_types: Optional[list[str]] = Field(
        default=None,
        description=(
            "Data type for each column: text, integer, float, boolean, json, "
            "array, image, images, datetime, audio, document. "
            "Must match length of columns. Defaults to 'text' for all."
        ),
    )
    number_of_rows: Optional[int] = Field(
        default=None,
        ge=0,
        le=MAX_MANUAL_ROWS,
        description=(
            "Requested row count for planning only. This tool does not create "
            "blank rows unless create_blank_rows is true. For synthetic or "
            "generated data, create the dataset first, then call "
            "add_dataset_rows with exactly this many populated row objects."
        ),
    )
    create_blank_rows: bool = Field(
        default=False,
        description=(
            "Set true only when the user explicitly asks for blank placeholder "
            "rows. Leave false for generated/synthetic datasets."
        ),
    )

    @field_validator("columns", mode="before")
    @classmethod
    def normalize_columns(cls, v):
        """Handle LLMs sending columns as dicts or stringified JSON."""
        import json as _json

        # Handle stringified JSON
        if isinstance(v, str):
            try:
                v = _json.loads(v)
            except (ValueError, TypeError):
                return [v]

        if not isinstance(v, list):
            return v

        # Handle list of dicts like [{"name": "col1", "type": "text"}]
        normalized = []
        for item in v:
            if isinstance(item, dict):
                normalized.append(item.get("name", item.get("column_name", str(item))))
            else:
                normalized.append(str(item))
        return normalized


VALID_DATA_TYPES = {
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
    "others",
    "persona",
}


@register_tool
class CreateDatasetTool(BaseTool):
    name = "create_dataset"
    description = (
        "Creates a new empty dataset with the specified columns. "
        "Returns the dataset ID for adding rows or running evaluations. "
        "For synthetic/generated data, call add_dataset_rows with populated "
        "rows after creation. number_of_rows is planning guidance only unless "
        "create_blank_rows=true."
    )
    category = "datasets"
    input_model = CreateDatasetInput

    def execute(self, params: CreateDatasetInput, context: ToolContext) -> ToolResult:

        from model_hub.services.dataset_service import ServiceError, create_dataset

        if not params.name or not params.columns:
            suggested_columns = ["input", "output", "expected_output", "context"]
            content = section(
                "Dataset Creation Requirements",
                (
                    "Provide `name` and `columns` to create a dataset. "
                    "For QA/evaluation workflows, a useful starting schema is shown below."
                ),
            )
            content += "\n\n" + markdown_table(
                ["Suggested Field", "Value"],
                [
                    ["name", "`falcon_<purpose>_<short_suffix>`"],
                    ["columns", ", ".join(f"`{column}`" for column in suggested_columns)],
                ],
            )
            return ToolResult(
                content=content,
                data={
                    "requires_name": not bool(params.name),
                    "requires_columns": not bool(params.columns),
                    "suggested_columns": suggested_columns,
                },
            )

        # Validate column types if provided
        if params.column_types:
            if len(params.column_types) != len(params.columns):
                return ToolResult.error(
                    f"column_types length ({len(params.column_types)}) must match "
                    f"columns length ({len(params.columns)}).",
                    error_code="VALIDATION_ERROR",
                )
            for ct in params.column_types:
                if ct not in VALID_DATA_TYPES:
                    return ToolResult.error(
                        f"Invalid column type '{ct}'. Valid types: {', '.join(sorted(VALID_DATA_TYPES))}",
                        error_code="VALIDATION_ERROR",
                    )

        types = params.column_types or ["text"] * len(params.columns)

        # Build column defs for service
        columns_def = [
            {"name": name, "data_type": dtype}
            for name, dtype in zip(params.columns, types)
        ]

        result = create_dataset(
            name=params.name,
            columns=columns_def,
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
        )

        if isinstance(result, ServiceError):
            # If name collision, include the existing dataset's ID so the agent can use it
            if "already exists" in result.message.lower():
                from model_hub.models.develop_dataset import Dataset

                existing = Dataset.objects.filter(
                    name=params.name,
                    organization=context.organization,
                ).first()
                if existing:
                    info = key_value_block(
                        [
                            ("Dataset ID", f"`{existing.id}`"),
                            ("Name", existing.name or "Untitled"),
                            ("Source", existing.source or "—"),
                            (
                                "Link",
                                dashboard_link(
                                    "dataset",
                                    str(existing.id),
                                    label="View existing dataset",
                                ),
                            ),
                        ]
                    )
                    return ToolResult(
                        content=section("Dataset Already Exists", info)
                        + "\n\nUse this dataset ID for the next step, or choose a different name if a new dataset is required.",
                        data={
                            "dataset_id": str(existing.id),
                            "name": existing.name,
                            "already_exists": True,
                        },
                    )
            return ToolResult.error(result.message, error_code=result.code)

        # Create empty rows only when explicitly requested. Falcon often sees
        # "generate 20 rows" and otherwise calls number_of_rows=20 before it has
        # actual values, which creates empty rows and makes the final dataset
        # under-filled.
        rows_created = 0
        if (
            params.number_of_rows
            and params.number_of_rows > 0
            and params.create_blank_rows
        ):
            from model_hub.services.dataset_service import add_dataset_rows

            empty_rows = [{} for _ in range(params.number_of_rows)]
            row_result = add_dataset_rows(
                dataset_id=result["dataset_id"],
                rows=empty_rows,
                organization=context.organization,
                workspace=context.workspace,
            )
            if isinstance(row_result, ServiceError):
                return ToolResult.error(row_result.message, error_code=row_result.code)
            rows_created = row_result["rows_added"]

        info = key_value_block(
            [
                ("Dataset ID", f"`{result['dataset_id']}`"),
                ("Name", result["name"]),
                ("Columns", str(len(result["columns"]))),
                (
                    "Column Details",
                    ", ".join(
                        f"`{c['name']}` ({c['data_type']})" for c in result["columns"]
                    ),
                ),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Dataset Created", info)
        if rows_created:
            content += f"\n\n_Created {rows_created} empty row(s)._"
        elif params.number_of_rows and params.number_of_rows > 0:
            content += (
                f"\n\n_Dataset schema is ready. Now call `add_dataset_rows` "
                f"with {params.number_of_rows} populated row object(s); no "
                "blank placeholder rows were created._"
            )
        else:
            content += "\n\n_Dataset is empty. Add rows via the dashboard or API._"

        return ToolResult(
            content=content,
            data={
                "dataset_id": result["dataset_id"],
                "name": result["name"],
                "columns": [
                    {"id": c["id"], "name": c["name"], "type": c["data_type"]}
                    for c in result["columns"]
                ],
                "rows_created": rows_created,
                "requested_row_count": params.number_of_rows or 0,
                "requires_add_dataset_rows": bool(
                    params.number_of_rows and not params.create_blank_rows
                ),
            },
        )
