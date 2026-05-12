import uuid as uuid_mod

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class ColumnDefinition(PydanticBaseModel):
    name: str = Field(max_length=50, description="Column name")
    data_type: str = Field(
        description=(
            "Data type of the column. Valid types: text, boolean, integer, "
            "float, json, array, image, images, datetime, audio, document, "
            "others, persona."
        )
    )
    description: str = Field(max_length=200, description="Column description")

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = str(v or "").strip()[:50]
        if not v.strip():
            raise ValueError("Column name cannot be empty or just whitespace.")
        return v

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: str) -> str:
        v = str(v or "").strip()[:200]
        if not v.strip():
            raise ValueError("Column description cannot be empty or just whitespace.")
        return v

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, v: str) -> str:
        from model_hub.models.choices import DataTypeChoices

        valid_types = [choice.value for choice in DataTypeChoices]
        if v not in valid_types:
            raise ValueError(
                f"Invalid data_type '{v}'. Valid types are: {', '.join(valid_types)}"
            )
        return v


class AddScenarioColumnsInput(PydanticBaseModel):
    scenario_id: uuid_mod.UUID | None = Field(
        default=None, description="The UUID of the scenario to add columns to"
    )
    columns: list[ColumnDefinition] = Field(
        default_factory=list,
        max_length=10,
        description="Columns to add (1-10)",
    )

    @model_validator(mode="after")
    def validate_no_duplicate_names(self) -> "AddScenarioColumnsInput":
        names = [col.name for col in self.columns]
        dupes = [n for n in names if names.count(n) > 1]
        if dupes:
            raise ValueError(f"Duplicate column name(s): {', '.join(set(dupes))}")
        return self


@register_tool
class AddScenarioColumnsTool(BaseTool):
    name = "add_scenario_columns"
    description = (
        "Adds new AI-generated columns to an existing scenario's dataset. "
        "Each column requires a name, data_type, and description. "
        "Maximum 10 columns per request."
    )
    category = "simulation"
    input_model = AddScenarioColumnsInput

    def _requirements_result(
        self,
        context: ToolContext,
        message: str = "",
        *,
        status: str = "needs_input",
        blocked_reason: str | None = None,
    ) -> ToolResult:
        from simulate.models.scenarios import Scenarios

        scenarios = Scenarios.objects.filter(
            organization=context.organization, deleted=False
        ).order_by("-created_at")[:10]
        rows = [f"- `{scenario.id}` — {scenario.name}" for scenario in scenarios]
        data = {
            "requires_scenario_id": True,
            "requires_columns": True,
            "scenarios": [
                {"id": str(scenario.id), "name": scenario.name}
                for scenario in scenarios
            ],
        }
        if blocked_reason:
            data["blocked_reason"] = blocked_reason
        return ToolResult(
            content=section(
                "Scenario Columns Requirements",
                (
                    (message + "\n\n" if message else "")
                    + "Provide `scenario_id` and `columns` with name, data_type, and description.\n\n"
                    + ("\n".join(rows) if rows else "No scenarios found.")
                ),
            ),
            data=data,
            status=status,
        )

    def execute(
        self, params: AddScenarioColumnsInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction
        from model_hub.models.choices import CellStatus, SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Row
        from simulate.models.scenarios import Scenarios

        from tfc.temporal.simulate import start_add_columns_workflow_sync

        if params.scenario_id is None or not params.columns:
            return self._requirements_result(context)

        try:
            scenario = Scenarios.objects.get(
                id=params.scenario_id,
                organization=context.organization,
                deleted=False,
            )
        except Scenarios.DoesNotExist:
            return self._requirements_result(
                context, f"Scenario `{params.scenario_id}` was not found."
            )

        if not scenario.dataset:
            return self._requirements_result(
                context,
                (
                    f"Scenario `{scenario.id}` does not have an associated dataset. "
                    "Choose a scenario with a dataset, or create/populate a scenario "
                    "dataset before adding AI-generated columns."
                ),
                status="blocked",
                blocked_reason="scenario_missing_dataset",
            )

        dataset = scenario.dataset

        # Check for duplicates against existing columns
        existing_names = set(
            Column.objects.filter(dataset=dataset, deleted=False).values_list(
                "name", flat=True
            )
        )
        for col in params.columns:
            if col.name in existing_names:
                return ToolResult.error(
                    f"Column '{col.name}' already exists in the dataset.",
                    error_code="VALIDATION_ERROR",
                )

        # Check dataset has rows
        row_ids = list(
            Row.objects.filter(dataset=dataset, deleted=False).values_list(
                "id", flat=True
            )
        )
        if not row_ids:
            return ToolResult.error(
                "Dataset has no rows. Cannot add columns to an empty dataset.",
                error_code="VALIDATION_ERROR",
            )

        columns_info = [
            {
                "name": c.name,
                "data_type": c.data_type,
                "description": c.description,
            }
            for c in params.columns
        ]

        # Create new columns
        new_columns = []
        new_column_ids = []
        for col_info in columns_info:
            col_id = uuid_mod.uuid4()
            new_columns.append(
                Column(
                    id=col_id,
                    dataset=dataset,
                    name=col_info["name"],
                    data_type=col_info["data_type"],
                    source=SourceChoices.OTHERS.value,
                    status=StatusType.RUNNING.value,
                    metadata={"description": col_info.get("description", "")},
                )
            )
            new_column_ids.append(str(col_id))

        Column.objects.bulk_create(new_columns)

        # Update dataset column_order and column_config
        with transaction.atomic():
            dataset.refresh_from_db()
            current_column_order = dataset.column_order or []
            current_column_config = dataset.column_config or {}

            for col_id, col_info in zip(new_column_ids, columns_info, strict=True):
                current_column_order.append(col_id)
                current_column_config[col_id] = {
                    "name": col_info["name"],
                    "type": col_info["data_type"],
                    "description": col_info.get("description", ""),
                }

            dataset.column_order = current_column_order
            dataset.column_config = current_column_config
            dataset.save()

        # Bulk create empty cells for all rows and new columns
        new_cells = []
        for row_id in row_ids:
            for column_id in new_column_ids:
                new_cells.append(
                    Cell(
                        id=uuid_mod.uuid4(),
                        dataset=dataset,
                        column_id=column_id,
                        row_id=row_id,
                        value=None,
                        status=CellStatus.RUNNING.value,
                    )
                )

        Cell.objects.bulk_create(new_cells)

        # Trigger the Temporal workflow to generate column data
        start_add_columns_workflow_sync(
            dataset_id=str(dataset.id),
            scenario_id=str(scenario.id),
            columns_info=columns_info,
            column_ids=new_column_ids,
        )

        info = key_value_block(
            [
                ("Scenario", f"`{scenario.id}`"),
                ("Dataset", f"`{dataset.id}`"),
                (
                    "Columns Added",
                    ", ".join(c.name for c in params.columns),
                ),
                ("Status", "Processing"),
            ]
        )

        content = section("Scenario Columns Added", info)
        content += "\n\n_Columns are being generated asynchronously. Use `get_scenario` to check status._"

        return ToolResult(
            content=content,
            data={
                "scenario_id": str(scenario.id),
                "dataset_id": str(dataset.id),
                "columns": [c.name for c in params.columns],
            },
        )
