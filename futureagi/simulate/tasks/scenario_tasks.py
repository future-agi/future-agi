import asyncio
import json
import math
import random
import traceback
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import structlog
from django.db import close_old_connections

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
        EnhancedScenariosAgent,
    )
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )
except ImportError:
    EnhancedScenariosAgent = _ee_stub("EnhancedScenariosAgent")
    SyntheticDataAgent = _ee_stub("SyntheticDataAgent")
from agentic_eval.core.llm.llm import LLM

logger = structlog.get_logger(__name__)
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import Scenarios
from simulate.models.scenario_graph import NodeType, ScenarioGraph
from tfc.temporal.drop_in import temporal_activity


def generate_scenario_columns(
    dataset_id: str,
    new_columns_required_info: list[dict],
    scenario_id: str | None = None,
) -> pd.DataFrame:
    """
    Generate values for the provided column definitions using the SyntheticDataAgent.

    Args:
        dataset_id: Dataset identifier used to gather requirements and reference rows.
        new_columns_required_info: List of column definitions (name, data_type, description, constraints).
        scenario_id: Optional scenario identifier for additional context (agent definition, etc.).

    Returns:
        pd.DataFrame: DataFrame with generated values for each requested column. The index aligns
                      with the row IDs used for generation.
    """
    try:
        close_old_connections()

        agent = SyntheticDataAgent()
        dataset = Dataset.objects.get(id=dataset_id)
        scenario = (
            Scenarios.objects.filter(id=scenario_id)
            .select_related("agent_definition")
            .first()
            if scenario_id
            else Scenarios.objects.filter(dataset_id=dataset_id)
            .select_related("agent_definition")
            .first()
        )
        agent_definition = getattr(scenario, "agent_definition", None)

        if not new_columns_required_info:
            raise ValueError(
                "new_columns_required_info must contain at least one column definition."
            )

        # Gather ordered rows for the dataset
        row_qs = Row.objects.filter(dataset=dataset).order_by("order")
        row_ids = list(row_qs.values_list("id", flat=True))
        if not row_ids:
            raise ValueError("Dataset has no rows to use as reference data.")

        new_column_names = {
            col.get("name") for col in new_columns_required_info if col.get("name")
        }

        # Build dataset-level requirements
        dataset_config = (
            dataset.synthetic_dataset_config or dataset.dataset_config or {}
        )
        dataset_metadata = (
            dataset_config.get("dataset", {})
            if isinstance(dataset_config, dict)
            else {}
        )
        dataset_description = dataset_metadata.get("description") or getattr(
            scenario, "description", ""
        )
        agent_name = getattr(agent_definition, "agent_name", "")
        agent_description = getattr(agent_definition, "description", "")
        agent_language = getattr(agent_definition, "language", "")
        dataset_objective = dataset_metadata.get("objective") or (
            f"Generate realistic values for the newly added columns within scenario '{scenario.name}' "
            f"for agent '{agent_name}' focused on {agent_description}."
            if scenario and agent_definition
            else "Generate realistic values for the newly added columns."
        )
        dataset_patterns = dataset_metadata.get("patterns") or (
            f"Maintain consistency with agent language ({agent_language}) and purpose."
            if agent_language
            else ""
        )

        requirements = {
            "Dataset Name": dataset.name,
            "Dataset Description": dataset_description,
            "Objective": dataset_objective,
            "patterns": dataset_patterns,
        }

        # Build constraints and schema from column definitions
        constraints: list[dict] = []
        schema: dict[str, dict[str, str]] = {}
        for column in new_columns_required_info:
            field_name = column.get("name")
            field_type = column.get("data_type")
            if not field_name or not field_type:
                raise ValueError(
                    "Each column definition must include 'name' and 'data_type'."
                )

            constraint = {
                "field": field_name,
                "type": field_type,
                "content": column.get(
                    "description",
                    f"Generate a plausible value for the column '{field_name}'.",
                ),
            }

            property_block = column.get("property", {})
            if isinstance(property_block, dict):
                for key, value in property_block.items():
                    if value is not None:
                        constraint[key.replace("_", " ")] = value

            for key, value in column.items():
                if key in {"name", "data_type", "description", "property"}:
                    continue
                if value is None or key in {"skip", "is_new"}:
                    continue
                constraint[key.replace("_", " ")] = value

            constraints.append(constraint)
            schema[field_name] = {"type": field_type}

        # Build reference data as a list of row dictionaries (excluding the new columns)
        cells = (
            Cell.objects.filter(dataset=dataset, row__id__in=row_ids)
            .select_related("column")
            .values("row_id", "column__name", "value")
        )
        cells_by_row: dict = defaultdict(dict)
        for cell in cells:
            cells_by_row[cell["row_id"]][cell["column__name"]] = cell["value"]

        reference_rows: list[dict] = []
        for row_id in row_ids:
            row_data = dict(cells_by_row.get(row_id, {}))
            for new_col_name in new_column_names:
                row_data.pop(new_col_name, None)
            reference_rows.append(row_data)

        if not reference_rows:
            raise ValueError(
                "Unable to construct reference data for column generation."
            )

        payload = {
            "requirements": requirements,
            "constraints": constraints,
            "schema": schema,
            "reference_data": reference_rows,
            "batch_size": len(reference_rows),
        }

        logger.info(
            "Generating scenario columns for dataset %s (%d columns, %d reference rows)",
            dataset_id,
            len(new_columns_required_info),
            len(reference_rows),
        )

        synthetic_columns = asyncio.run(agent.generate_column_data(payload))

        # Align output index with the row IDs used during generation
        if len(synthetic_columns) == len(row_ids):
            synthetic_columns.index = row_ids

        return synthetic_columns

    finally:
        close_old_connections()


@temporal_activity(
    time_limit=3600,
    max_retries=0,
    queue="tasks_xl",
)
def add_scenario_columns_task(
    dataset_id: str,
    scenario_id: str,
    new_columns_info: list[dict],
    new_column_ids: list[str],
) -> dict:
    """
    Temporal activity to add new columns to an existing scenario dataset.

    Args:
        dataset_id: ID of the dataset to add columns to
        scenario_id: ID of the scenario for context
        new_columns_info: List of column definitions (name, data_type, description)
        new_column_ids: List of pre-created column IDs

    Returns:
        dict: Status information about the column generation
    """
    try:
        close_old_connections()

        dataset = Dataset.objects.get(id=dataset_id)
        scenario = Scenarios.objects.get(id=scenario_id)

        logger.info(
            "Starting add_scenario_columns_task for dataset %s, scenario %s, columns: %s",
            dataset_id,
            scenario_id,
            [col.get("name") for col in new_columns_info],
        )

        # Generate data for the new columns using existing function
        synthetic_df = generate_scenario_columns(
            dataset_id=dataset_id,
            new_columns_required_info=new_columns_info,
            scenario_id=scenario_id,
        )

        # Update cells with generated data
        columns_by_name = {
            col.name: col for col in Column.objects.filter(id__in=new_column_ids)
        }

        cells_to_update = []
        for row_id in synthetic_df.index:
            for col_name in synthetic_df.columns:
                if col_name not in columns_by_name:
                    continue

                column = columns_by_name[col_name]
                value = synthetic_df.loc[row_id, col_name]

                # Find or create the cell
                cell, created = Cell.objects.get_or_create(
                    dataset=dataset,
                    column=column,
                    row_id=row_id,
                    defaults={
                        "id": uuid.uuid4(),
                        "value": value,
                        "status": CellStatus.PASS.value,
                    },
                )

                if not created:
                    cell.value = value
                    cell.status = CellStatus.PASS.value
                    cells_to_update.append(cell)

        # Bulk update cells
        if cells_to_update:
            Cell.objects.bulk_update(cells_to_update, ["value", "status"])

        # Update column statuses to completed
        Column.objects.filter(id__in=new_column_ids).update(
            status=StatusType.COMPLETED.value
        )

        logger.info(
            "Successfully completed add_scenario_columns_task for dataset %s",
            dataset_id,
        )

        return {
            "status": "success",
            "dataset_id": dataset_id,
            "columns_added": len(new_columns_info),
            "rows_updated": len(synthetic_df),
        }

    except Exception as e:
        logger.error(
            "Error in add_scenario_columns_task for dataset %s: %s",
            dataset_id,
            str(e),
        )
        logger.error(traceback.format_exc())

        # Update column statuses to failed
        try:
            Column.objects.filter(id__in=new_column_ids).update(
                status=StatusType.FAILED.value
            )
        except Exception:
            pass

        raise e
    finally:
        close_old_connections()


def _resolve_knowledge_base_payload(agent_definition, description):
    """Return the SDA-shaped knowledge_base dict for an agent, or None.

    Reads the agent's linked KB (if any), resolves it to a subset of
    doc_ids via the KB indexer, and formats the result as the payload
    the scenario generator forwards to the synthetic-data agent.
    """
    kb_id = getattr(agent_definition, "knowledge_base_id", None)
    if not kb_id:
        return None
    try:
        from model_hub.utils.kb_indexer import KB_TABLE_NAME, KBIndexer

        doc_ids = KBIndexer().get_subset_kb_id(description or "", str(kb_id))
        doc_ids = [str(d) for d in doc_ids] if doc_ids else []
    except Exception as exc:
        logger.warning(
            "scenario_kb_resolve_failed",
            kb_id=str(kb_id),
            error=str(exc),
        )
        return None
    if not doc_ids:
        return None
    return {
        "table_name": KB_TABLE_NAME,
        "kb_id": str(kb_id),
        "doc_ids": doc_ids,
    }


def generate_scenario_rows(
    dataset_id,
    scenario_id,
    num_rows,
    description,
    new_rows_id,
    sample_size_reference_data=5,
):
    """
    Generate new rows for scenario datasets with special constraints for persona, situation, and outcome columns.

    Args:
        dataset_id: ID of the dataset to add rows to
        scenario_id: ID of the scenario (used to get agent definition for constraints)
        num_rows: Number of rows to generate
        description: Description for generating the rows
        new_rows_id: List of new row IDs
        sample_size_reference_data: Number of reference rows to sample (default: 5)
    """
    try:
        close_old_connections()

        dataset = Dataset.objects.get(id=dataset_id)
        scenario = Scenarios.objects.get(id=scenario_id)
        custom_instruction = None
        persona_ids = None
        if scenario.metadata:
            metadata = scenario.metadata
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            persona_ids = metadata.get("persona_ids", None)
            custom_instruction = metadata.get("custom_instruction", None)
        # Get agent definition for constraints
        agent_definition = scenario.agent_definition

        # Resolve the agent's KB (if any) into the payload shape the generator expects.
        knowledge_base_payload = _resolve_knowledge_base_payload(
            agent_definition, description
        )

        # Determine simulation mode
        mode = "voice" if agent_definition.agent_type == "voice" else "chat"

        # Get all columns (excluding experiment columns)
        total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
            source__in=[
                SourceChoices.EXPERIMENT.value,
                SourceChoices.EXPERIMENT_EVALUATION.value,
                SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            ]
        )

        scenario_base_columns = {
            "persona",
            "situation",
            "outcome",
            "conversation_branch",
            "branch_category",
        }
        custom_columns: list[dict] = []
        for col in total_columns:
            if col.name.lower() in scenario_base_columns:
                continue
            custom_columns.append(
                {
                    "name": col.name,
                    "data_type": col.data_type,
                    "description": f"Generate appropriate values for {col.name}",
                }
            )

        # Instantiate EnhancedScenariosAgent - it handles everything!
        scenario_agent = EnhancedScenariosAgent(
            agent_definition_id=agent_definition.id,
            no_of_rows=num_rows,
            simulation_mode=mode,
            custom_columns=custom_columns,
            knowledge_base=knowledge_base_payload,
        )
        if custom_instruction:
            scenario_agent.custom_instruction = custom_instruction

        # Convert personas to property list if available
        property_list = []
        if persona_ids:
            from simulate.views.scenarios import convert_personas_to_property_list

            property_list = convert_personas_to_property_list(persona_ids)

        # Fetch branches and generate cases using the agent's complete pipeline
        # Handles: branch extraction, parallel processing, SDA payload, validation, enrichment
        scenario_graph = ScenarioGraph.objects.get(scenario=scenario)
        branches = scenario_agent.graph_generator.get_branches(
            graph_id=scenario_graph.id
        )

        cases = scenario_agent._generate_cases_for_branches(
            branches=branches,
            user_requirements={},
            graph_id=scenario_graph.id,
            property_list=property_list,
            mode=mode,
        )

        if not cases:
            raise ValueError("No cases were generated by the scenario agent")

        # Bulk update cells with generated values
        logger.info(
            f"Generated {len(cases)} cases, updating cells for {num_rows} rows..."
        )

        target_row_ids = new_rows_id[: min(len(cases), num_rows)]
        existing_cells = {
            (str(cell.row_id), str(cell.column_id)): cell
            for cell in Cell.objects.filter(
                dataset=dataset,
                column__in=total_columns,
                row_id__in=target_row_ids,
            )
        }

        cells_to_update = []
        for i in range(min(len(cases), num_rows)):
            case = cases[i]
            row_id = new_rows_id[i]
            for col in total_columns:
                try:
                    col_lower = col.name.lower()
                    if col_lower == "conversation_branch":
                        value = case.get(
                            "conversation_branch", case.get("branch_name", "")
                        )
                    else:
                        # Try lowercase first, then exact name
                        value = case.get(col_lower, case.get(col.name, ""))

                    if value is None:
                        value = ""
                except Exception:
                    value = ""

                cell = existing_cells.get((str(row_id), str(col.id)))
                if cell is not None:
                    cell.value = value
                    cell.status = CellStatus.PASS.value
                    cells_to_update.append(cell)
                else:
                    cells_to_update.append(
                        Cell(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column=col,
                            row_id=row_id,
                            value=value,
                            status=CellStatus.PASS.value,
                        )
                    )

        # Bulk update all cells in one query (or create if needed)
        if cells_to_update:
            # Separate existing cells (for update) from new cells (for create)
            cells_with_pk = [c for c in cells_to_update if c.pk is not None]
            cells_without_pk = [c for c in cells_to_update if c.pk is None]

            if cells_with_pk:
                Cell.objects.bulk_update(
                    cells_with_pk, ["value", "status"], batch_size=500
                )
            if cells_without_pk:
                Cell.objects.bulk_create(cells_without_pk, batch_size=500)

        # Update all column statuses to completed in one query
        total_columns.update(status=StatusType.COMPLETED.value)

        logger.info(
            f"Successfully generated {num_rows} scenario rows for dataset {dataset_id}"
        )

    except Exception as e:
        logger.error(f"Error generating scenario rows: {str(e)}")
        traceback.print_exc()

        # Mark cells as failed using bulk operations
        try:
            dataset = Dataset.objects.get(id=dataset_id)
            total_columns = Column.objects.filter(
                dataset=dataset, deleted=False
            ).exclude(
                source__in=[
                    SourceChoices.EXPERIMENT.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]
            )

            existing_error_cells = Cell.objects.filter(
                dataset=dataset,
                column__in=total_columns,
                row_id__in=new_rows_id,
            )
            cells_to_fail = []
            for cell in existing_error_cells:
                cell.status = CellStatus.ERROR.value
                cells_to_fail.append(cell)

            # Bulk update all failed cells
            if cells_to_fail:
                Cell.objects.bulk_update(cells_to_fail, ["status"], batch_size=500)

            # Update all columns to failed status in one query
            total_columns.update(status=StatusType.FAILED.value)

        except Exception as cleanup_error:
            logger.error(f"Error during cleanup: {str(cleanup_error)}")

        raise

    finally:
        close_old_connections()
