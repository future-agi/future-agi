"""Dataset persistence — create Dataset + Columns + Rows + Cells.

Replaces ESA methods: _create_scenario_dataset, create_scenario_dataset_from_cases.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

import structlog

from django.db import transaction

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.choices import (
    DataTypeChoices,
    DatasetSourceChoices,
    SourceChoices,
)
from simulate.models import Scenarios

logger = structlog.get_logger(__name__)


def create_scenario_dataset(
    scenario_id: str,
    cases: List[Dict[str, Any]],
    name: str,
    description: str,
    agent_context: Dict[str, Any],
    custom_columns: Optional[List[Dict[str, Any]]] = None,
) -> Dataset:
    """Create a Dataset with scenario data stored as rows.

    All DB writes happen inside a single transaction.atomic() block.

    Args:
        scenario_id: ID of the parent Scenarios record.
        cases: Validated case dicts to persist.
        name: Dataset name prefix.
        description: Dataset description.
        agent_context: Flat dict with agent_definition_id, agent_name, etc.
        custom_columns: Optional custom column definitions.

    Returns:
        Created Dataset instance.

    Raises:
        Exception: On DB errors (transaction rolled back).
    """
    scenario = Scenarios.objects.get(id=scenario_id)

    agent_definition_id = agent_context.get("agent_definition_id", "")
    agent_name = agent_context.get("agent_name", "")

    try:
        with transaction.atomic():
            # Create the dataset
            dataset = Dataset.objects.create(
                id=uuid.uuid4(),
                name=f"{name} - Scenario Dataset",
                organization=scenario.organization,
                workspace=scenario.workspace,
                source=DatasetSourceChoices.SCENARIO.value,
                column_order=[],
                column_config={},
                dataset_config={
                    "scenario_type": "graph",
                    "agent_definition_id": str(agent_definition_id),
                    "agent_name": agent_name,
                    "description": description,
                    "total_cases": len(cases),
                },
            )

            # Define column schema
            column_schema = {
                "persona": {
                    "data_type": DataTypeChoices.PERSONA.value,
                    "description": "Customer persona profile",
                },
                "situation": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Customer situation or scenario",
                },
                "outcome": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Conversation outcome",
                },
                "conversation_branch": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Branch name in workflow graph",
                },
                "branch_category": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Type of branch in the scenario graph",
                },
            }

            # Add custom columns to the schema
            if custom_columns:
                for column in custom_columns:
                    column_name = column.get("name")
                    column_type = column.get("data_type", "text")
                    column_description = column.get("description", "")

                    data_type_value = column_type.upper()
                    if hasattr(DataTypeChoices, data_type_value):
                        data_type_value = getattr(
                            DataTypeChoices, data_type_value
                        ).value
                    else:
                        data_type_value = DataTypeChoices.TEXT.value

                    column_schema[column_name] = {
                        "data_type": data_type_value,
                        "description": column_description,
                    }

            # Determine agent_type for persona column metadata
            agent_type = (
                scenario.agent_definition.agent_type
                if scenario.agent_definition
                else "text"
            )

            # Create columns and store references
            columns_by_name: Dict[str, Column] = {}
            column_uuids: List[str] = []
            column_config: Dict[str, Any] = {}

            for col_name, col_info in column_schema.items():
                column: Column = Column.objects.create(
                    id=uuid.uuid4(),
                    dataset=dataset,
                    name=col_name,
                    data_type=col_info["data_type"],
                    source=SourceChoices.OTHERS.value,
                    metadata={
                        "description": col_info["description"],
                        "simulation_type": agent_type,
                    },
                )
                columns_by_name[col_name] = column
                column_uuids.append(str(column.id))
                column_config[str(column.id)] = {
                    "name": col_name,
                    "type": col_info["data_type"],
                    "description": col_info["description"],
                }

                if col_name == "persona":
                    column_config[str(column.id)]["simulation_type"] = (
                        scenario.agent_definition.agent_type
                        if scenario.agent_definition
                        else None
                    )

            # Update dataset with column_order and column_config
            dataset.column_order = column_uuids
            dataset.column_config = column_config
            dataset.save()

            # Create rows and cells for each case
            for i, case in enumerate(cases):
                row: Row = Row.objects.create(
                    id=uuid.uuid4(),
                    dataset=dataset,
                    order=i,
                    metadata={"intent_id": case.get("intent_id")},
                )

                conversation_flow = case.get("nodes", [])

                # Create cells for each column
                for col_name, col in columns_by_name.items():
                    if col_name == "persona":
                        value = case.get("persona", {})
                    elif col_name == "situation":
                        value = case.get("situation", "")
                    elif col_name == "outcome":
                        value = case.get("outcome", "")
                    elif col_name == "conversation_branch":
                        value = case.get("conversation_branch", "")
                    elif col_name == "branch_category":
                        value = case.get("branch_category", "")
                    elif col_name == "conversation_flow":
                        value = json.dumps(conversation_flow)
                    elif col_name == "scenario_flow":
                        value = json.dumps(case.get("detailed_path", []))
                    elif col_name == "branch_type":
                        value = case.get("branch_type", "")
                    elif col_name == "branch_id":
                        value = case.get("branch_id", "")
                    elif col_name == "complexity":
                        value = case.get("complexity", "")
                    else:
                        # Custom column
                        value = case.get(col_name, "")

                    Cell.objects.create(
                        id=uuid.uuid4(),
                        dataset=dataset,
                        column=col,
                        row=row,
                        value=value,
                    )

            logger.info(
                f"Successfully created dataset {dataset.id} with {len(cases)} cases"
            )
            return dataset

    except Exception as e:
        logger.exception(f"Error creating scenario dataset: {str(e)}")
        raise e
