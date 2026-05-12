from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class AddScenarioRowsInput(PydanticBaseModel):
    scenario_id: str = Field(
        description="The UUID or name of the scenario to add rows to"
    )
    num_rows: int = Field(
        ge=10,
        le=100,
        description="Number of rows to generate (10-100)",
    )
    description: str = Field(
        description="Description to guide AI row generation",
    )

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description cannot be empty or just whitespace.")
        return v.strip()

    @field_validator("scenario_id", mode="before")
    @classmethod
    def normalize_scenario_id(cls, value) -> str:
        return "" if value is None else str(value).strip()


@register_tool
class AddScenarioRowsTool(BaseTool):
    name = "add_scenario_rows"
    description = (
        "Adds new AI-generated rows to an existing scenario's dataset. "
        "Requires a description to guide the generation. "
        "Generates between 10 and 100 rows."
    )
    category = "simulation"
    input_model = AddScenarioRowsInput

    def execute(self, params: AddScenarioRowsInput, context: ToolContext) -> ToolResult:
        from django.db.models import Max
        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Row
        from ai_tools.tools.simulation._utils import resolve_scenario

        from tfc.temporal.simulate import start_add_scenario_rows_workflow_sync

        scenario, scenario_result = resolve_scenario(
            params.scenario_id,
            context,
            title="Scenario Required",
        )
        if scenario_result:
            return scenario_result

        if not scenario.dataset:
            return ToolResult(
                content=section(
                    "Scenario Has No Dataset",
                    (
                        f"Scenario `{scenario.name}` exists, but it does not have "
                        "an associated dataset to add generated rows into. Create "
                        "or attach a dataset-backed scenario before adding rows."
                    ),
                ),
                data={
                    "scenario_id": str(scenario.id),
                    "scenario_name": scenario.name,
                    "requires_dataset": True,
                },
            )

        dataset = scenario.dataset

        # Get columns (excluding experiment columns)
        total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
            source__in=[
                SourceChoices.EXPERIMENT.value,
                SourceChoices.EXPERIMENT_EVALUATION.value,
                SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            ]
        )

        # Get the max order for new rows
        max_order = (
            Row.objects.filter(dataset=dataset, deleted=False).aggregate(Max("order"))[
                "order__max"
            ]
            or -1
        )

        # Bulk create new empty rows
        new_rows = []
        new_rows_id = []
        for i in range(params.num_rows):
            row_id = uuid_mod.uuid4()
            new_rows.append(Row(id=row_id, dataset=dataset, order=max_order + 1 + i))
            new_rows_id.append(str(row_id))

        Row.objects.bulk_create(new_rows)

        # Bulk create empty cells for all columns
        new_cells = []
        for row_id in new_rows_id:
            for col in total_columns:
                new_cells.append(
                    Cell(
                        id=uuid_mod.uuid4(),
                        dataset=dataset,
                        column=col,
                        row_id=row_id,
                        value=None,
                        status="running",
                    )
                )

        Cell.objects.bulk_create(new_cells)

        # Update columns status to running
        total_columns.update(status=StatusType.RUNNING.value)

        # Trigger the Temporal workflow to generate data
        start_add_scenario_rows_workflow_sync(
            dataset_id=str(dataset.id),
            scenario_id=str(scenario.id),
            num_rows=params.num_rows,
            row_ids=new_rows_id,
            description=params.description,
        )

        info = key_value_block(
            [
                ("Scenario", f"`{scenario.id}`"),
                ("Dataset", f"`{dataset.id}`"),
                ("Rows Added", str(params.num_rows)),
                ("Status", "Processing"),
            ]
        )

        content = section("Scenario Rows Added", info)
        content += "\n\n_Rows are being generated asynchronously. Use `get_scenario` to check status._"

        return ToolResult(
            content=content,
            data={
                "scenario_id": str(scenario.id),
                "dataset_id": str(dataset.id),
                "num_rows": params.num_rows,
            },
        )
