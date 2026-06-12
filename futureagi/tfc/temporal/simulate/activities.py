"""
Temporal activities for simulate app - Scenario related only.

Activities contain the actual business logic and can import Django models.
They are executed by workers outside the workflow sandbox.

Note: Test execution activities have been removed - using Celery tasks instead.

NOTE: Each activity should be idempotent where possible.
"""

import asyncio
import json
import traceback
import types
from typing import Any, Dict, List, Optional, Tuple, Union

import json_repair
import pandas as pd
import structlog
from django.conf import settings
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agentic_eval.core.utils.model_config import (
    LiteLlmProvider,
    ModelConfig,
    ModelConfigs,
)

logger = structlog.get_logger(__name__)

from accounts.models.user import User

# Use the activity-aware stub: invocations raise a Temporal non-retryable
# ApplicationError so the workflow fails once instead of retrying.
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.scenario_graph.persona_configurator import (
        PersonaConfigurator,
    )
except ImportError:
    PersonaConfigurator = _ee_stub("PersonaConfigurator")
try:
    from ee.usage.utils.event_properties import token_usage_properties
except ImportError:
    token_usage_properties = lambda token_usage: {}
from agentic_eval.core.llm.llm import LLM
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import Scenarios
from tfc.middleware.workspace_context import get_current_organization
from tfc.temporal.simulate.types import (  # Scenario Generation; Scenario Creation; Graph Scenario Sub-Activity Types (v2+v3)
    AddScenarioColumnsInput,
    CategorizeAndValidateInput,
    CategorizeAndValidateOutput,
    CategorizeBranchInput,
    CategorizeBranchOutput,
    CreateDatasetScenarioWorkflowInput,
    CreateDatasetScenarioWorkflowOutput,
    CreateGraphScenarioWorkflowInput,
    CreateGraphScenarioWorkflowOutput,
    CreateScenarioDatasetInput,
    CreateScenarioDatasetOutput,
    CreateScriptScenarioWorkflowInput,
    CreateScriptScenarioWorkflowOutput,
    ExtractIntentsInput,
    ExtractIntentsOutput,
    FinalizeGraphScenarioInput,
    FinalizeGraphScenarioOutput,
    GenerateCasesForIntentInput,
    GenerateCasesForIntentOutput,
    GenerateColumnDataInput,
    GenerateColumnDataOutput,
    GenerateScenarioRowsInput,
    GenerateSyntheticDataInput,
    GenerateSyntheticDataOutput,
    GetBranchesInput,
    GetBranchesOutput,
    PersistCellsInput,
    PersistCellsOutput,
    PersistColumnCellsInput,
    PersistColumnCellsOutput,
    PrepareScenarioInput,
    PrepareScenarioOutput,
    ProcessBranchesInput,
    ProcessBranchesOutput,
    ProcessSingleBranchInput,
    ProcessSingleBranchOutput,
    SelectBranchesInput,
    SelectBranchesOutput,
    SetupColumnsInput,
    SetupColumnsOutput,
    SetupGenerationInput,
    SetupGenerationOutput,
    SetupGraphScenarioInput,
    SetupGraphScenarioOutput,
    ValidateAndEnrichCasesInput,
    ValidateAndEnrichCasesOutput,
    ValidatePersonasInput,
    ValidatePersonasOutput,
)

# Redis TTL for large payloads passed between activities (6 hours)
SCENARIO_PAYLOAD_TTL = 21600

# =============================================================================
# Scenario Generation Activities
# =============================================================================


@activity.defn
async def setup_generation_activity(
    input: SetupGenerationInput,
) -> SetupGenerationOutput:
    """
    Setup scenario row generation.

    Loads dataset, scenario, agent definition, and extracts conversation branches.
    """
    from django.db import close_old_connections

    try:
        close_old_connections()

        # TODO: Implement - extract from generate_scenario_rows task
        # This should:
        # 1. Load dataset, scenario, agent_definition
        # 2. Get graph data and extract branches
        # 3. Build generation payload (requirements, constraints, schema)
        # 4. Return the payload and branch metadata

        return SetupGenerationOutput(
            dataset_id=input.dataset_id,
            scenario_id=input.scenario_id,
            status="READY",
            generation_payload={},
            branch_metadata=[],
            column_names=[],
        )

    except Exception as e:
        activity.logger.exception(f"setup_generation_activity failed: {e}")
        return SetupGenerationOutput(
            dataset_id=input.dataset_id,
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )
    finally:
        close_old_connections()


@activity.defn
async def generate_synthetic_data_activity(
    input: GenerateSyntheticDataInput,
) -> GenerateSyntheticDataOutput:
    """
    Generate synthetic data using SyntheticDataAgent.

    Uses Heartbeater for automatic heartbeats during long-running generation.
    Uses ShutdownMonitor to detect worker shutdown and allow graceful retry.
    """
    from django.db import close_old_connections

    from tfc.temporal.common.heartbeat import Heartbeater
    from tfc.temporal.common.shutdown import ShutdownMonitor

    try:
        close_old_connections()

        if input.organization_id:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            usage_check = check_usage(
                input.organization_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                return GenerateSyntheticDataOutput(
                    status="FAILED",
                    error=usage_check.reason or "Usage limit exceeded",
                )
        else:
            logger.warning(
                "usage_check_skipped_no_org_id",
                activity="generate_synthetic_data_activity",
            )

        try:
            from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
                SyntheticDataAgent,
            )
        except ImportError:
            if settings.DEBUG:
                logger.warning("Could not import ee.agenthub.synthetic_data_agent.synthetic_data_agent", exc_info=True)
            return None

        async with Heartbeater() as heartbeater, ShutdownMonitor() as monitor:
            monitor.raise_if_is_worker_shutdown()
            heartbeater.details = ("generating", 0, 1)

            agent = SyntheticDataAgent()
            synthetic_df = agent.generate_and_validate(
                input.generation_payload,
                branch_metadatas=input.branch_metadata,
                called_for="simulate",
            )

            monitor.raise_if_is_worker_shutdown()
            heartbeater.details = ("converting", 1, 1)
            data = synthetic_df.to_dict(orient="records")

        if input.organization_id:
            try:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                actual_cost = agent.llm.cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(actual_cost)
                emit(
                    UsageEvent(
                        org_id=input.organization_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_scenario_generation",
                            "raw_cost_usd": str(actual_cost),
                            **token_usage_properties(agent.llm.token_usage),
                        },
                    )
                )
            except Exception:
                pass

        return GenerateSyntheticDataOutput(
            status="COMPLETED",
            data=data,
        )

    except Exception as e:
        activity.logger.exception(f"generate_synthetic_data_activity failed: {e}")
        return GenerateSyntheticDataOutput(
            status="FAILED",
            error=str(e),
        )
    finally:
        close_old_connections()


@activity.defn
async def validate_personas_activity(
    input: ValidatePersonasInput,
) -> ValidatePersonasOutput:
    """
    Validate persona fields and fill missing required fields with defaults.
    """
    import json
    import random

    try:
        default_values = {
            "gender": ["male", "female"],
            "age_group": ["18-25", "25-32", "32-40", "40-50", "50-60", "60+"],
            "location": [
                "United States",
                "Canada",
                "United Kingdom",
                "Australia",
                "India",
            ],
            "profession": [
                "Student",
                "Teacher",
                "Engineer",
                "Doctor",
                "Nurse",
                "Business Owner",
                "Manager",
                "Sales Representative",
            ],
            "personality": [
                "Friendly and cooperative",
                "Professional and formal",
                "Cautious and skeptical",
                "Impatient and direct",
            ],
            "communication_style": [
                "Direct and concise",
                "Detailed and elaborate",
                "Casual and friendly",
                "Formal and polite",
            ],
            "accent": ["American", "Australian", "Indian", "Canadian", "Neutral"],
            "language": ["English"],
            "conversation_speed": ["0.5", "0.75", "1.0", "1.25", "1.5"],
            "background_sound": ["true", "false"],
            "finished_speaking_sensitivity": [
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
            ],
            "interrupt_sensitivity": [
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
            ],
            "name": "Not Specified",
        }

        validated_personas = []

        for persona in input.personas:
            # Convert string to dict if needed
            if isinstance(persona, str):
                try:
                    persona = json.loads(persona)
                    persona = {k.lower(): v for k, v in persona.items()}
                except Exception:
                    persona = {}

            if not isinstance(persona, dict):
                persona = {}

            # Fill missing required fields
            for field in input.required_fields:
                if field not in persona or not persona[field]:
                    if field in default_values:
                        value = default_values[field]
                        if isinstance(value, list):
                            persona[field] = random.choice(value)
                        else:
                            persona[field] = value
                    else:
                        persona[field] = "Not Specified"

            # Remove extra fields
            validated_persona = {
                k: v for k, v in persona.items() if k in input.required_fields
            }
            validated_personas.append(validated_persona)

        return ValidatePersonasOutput(
            status="COMPLETED",
            validated_personas=validated_personas,
        )

    except Exception as e:
        activity.logger.exception(f"validate_personas_activity failed: {e}")
        return ValidatePersonasOutput(
            status="FAILED",
            error=str(e),
        )


def _persist_cells_sync(
    dataset_id: str,
    row_ids: list,
    data: list,
    column_names: list,
) -> dict:
    """Synchronous implementation of persist_cells."""
    import uuid

    from django.db import close_old_connections

    try:
        close_old_connections()

        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Dataset

        dataset = Dataset.objects.get(id=dataset_id)
        columns = {
            col.name: col
            for col in Column.objects.filter(dataset=dataset, deleted=False)
        }

        cells_to_update = []
        cells_to_create = []

        for i, row_id in enumerate(row_ids):
            if i >= len(data):
                continue

            row_data = data[i]

            for col_name in column_names:
                if col_name not in columns:
                    continue

                column = columns[col_name]
                value = row_data.get(col_name, "")

                try:
                    cell = Cell.objects.get(
                        dataset=dataset,
                        column=column,
                        row_id=row_id,
                    )
                    cell.value = value
                    cell.status = CellStatus.PASS.value
                    cells_to_update.append(cell)
                except Cell.DoesNotExist:
                    cells_to_create.append(
                        Cell(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column=column,
                            row_id=row_id,
                            value=value,
                            status=CellStatus.PASS.value,
                        )
                    )

        if cells_to_update:
            Cell.objects.bulk_update(
                cells_to_update, ["value", "status"], batch_size=500
            )

        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create, batch_size=500)

        # Update column statuses
        Column.objects.filter(
            dataset=dataset,
            name__in=column_names,
        ).update(status=StatusType.COMPLETED.value)

        return {
            "status": "COMPLETED",
            "cells_created": len(cells_to_create),
            "cells_updated": len(cells_to_update),
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def persist_cells_activity(input: PersistCellsInput) -> PersistCellsOutput:
    """
    Persist generated data to Cell records.
    Uses Heartbeater for automatic heartbeats during long-running persistence.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _persist_cells_sync, thread_sensitive=False
            )(
                input.dataset_id,
                input.row_ids,
                input.data,
                input.column_names,
            )

        if result.get("status") == "FAILED":
            activity.logger.exception(
                f"persist_cells_activity failed: {result.get('error')}"
            )

        return PersistCellsOutput(
            status=result["status"],
            cells_created=result.get("cells_created", 0),
            cells_updated=result.get("cells_updated", 0),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"persist_cells_activity failed: {e}")
        return PersistCellsOutput(
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Add Columns Activities
# =============================================================================


@activity.defn
async def setup_columns_activity(input: SetupColumnsInput) -> SetupColumnsOutput:
    """
    Setup column generation.
    """
    from django.db import close_old_connections

    try:
        close_old_connections()

        # TODO: Implement - extract from add_scenario_columns_task
        # This should build the payload for SyntheticDataAgent.generate_column_data()

        return SetupColumnsOutput(
            status="READY",
            generation_payload={},
            row_ids=[],
        )

    except Exception as e:
        activity.logger.exception(f"setup_columns_activity failed: {e}")
        return SetupColumnsOutput(
            status="FAILED",
            error=str(e),
        )
    finally:
        close_old_connections()


@activity.defn
async def generate_column_data_activity(
    input: GenerateColumnDataInput,
) -> GenerateColumnDataOutput:
    """
    Generate data for new columns using SyntheticDataAgent.

    Uses Heartbeater for automatic heartbeats during long-running generation.
    Uses ShutdownMonitor to detect worker shutdown and allow graceful retry.
    """
    from django.db import close_old_connections

    from tfc.temporal.common.heartbeat import Heartbeater
    from tfc.temporal.common.shutdown import ShutdownMonitor

    try:
        close_old_connections()

        if input.organization_id:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            usage_check = check_usage(
                input.organization_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                return GenerateColumnDataOutput(
                    status="FAILED",
                    error=usage_check.reason or "Usage limit exceeded",
                )
        else:
            logger.warning(
                "usage_check_skipped_no_org_id",
                activity="generate_column_data_activity",
            )

        try:
            from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
                SyntheticDataAgent,
            )
        except ImportError:
            if settings.DEBUG:
                logger.warning("Could not import ee.agenthub.synthetic_data_agent.synthetic_data_agent", exc_info=True)
            return None

        async with Heartbeater() as heartbeater, ShutdownMonitor() as monitor:
            monitor.raise_if_is_worker_shutdown()
            heartbeater.details = ("generating_columns", 0, 1)

            agent = SyntheticDataAgent()
            synthetic_df = await agent.generate_column_data(input.generation_payload)

            monitor.raise_if_is_worker_shutdown()
            heartbeater.details = ("converting_results", 1, 1)

            data = {}
            for row_id in synthetic_df.index:
                data[str(row_id)] = {}
                for col_name in synthetic_df.columns:
                    data[str(row_id)][col_name] = synthetic_df.loc[row_id, col_name]

        if input.organization_id:
            try:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                actual_cost = agent.llm.cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(actual_cost)
                emit(
                    UsageEvent(
                        org_id=input.organization_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_column_generation",
                            "raw_cost_usd": str(actual_cost),
                            **token_usage_properties(agent.llm.token_usage),
                        },
                    )
                )
            except Exception:
                pass

        return GenerateColumnDataOutput(
            status="COMPLETED",
            data=data,
        )

    except Exception as e:
        activity.logger.exception(f"generate_column_data_activity failed: {e}")
        return GenerateColumnDataOutput(
            status="FAILED",
            error=str(e),
        )
    finally:
        close_old_connections()


def _persist_column_cells_sync(
    dataset_id: str,
    column_ids: list,
    data: dict,
) -> dict:
    """Synchronous implementation of persist_column_cells."""
    import uuid

    from django.db import close_old_connections

    try:
        close_old_connections()

        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Dataset

        dataset = Dataset.objects.get(id=dataset_id)
        columns = {col.id: col for col in Column.objects.filter(id__in=column_ids)}
        column_names = {col.name: col for col in columns.values()}

        cells_to_update = []

        for row_id, row_data in data.items():
            for col_name, value in row_data.items():
                if col_name not in column_names:
                    continue

                column = column_names[col_name]

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

        if cells_to_update:
            Cell.objects.bulk_update(cells_to_update, ["value", "status"])

        # Update column statuses
        Column.objects.filter(id__in=column_ids).update(
            status=StatusType.COMPLETED.value
        )

        return {
            "status": "COMPLETED",
            "cells_updated": len(cells_to_update),
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def persist_column_cells_activity(
    input: PersistColumnCellsInput,
) -> PersistColumnCellsOutput:
    """
    Persist generated column data to Cell records.

    Uses Heartbeater and ShutdownMonitor for graceful handling of large datasets.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater
    from tfc.temporal.common.shutdown import ShutdownMonitor

    try:
        async with Heartbeater(), ShutdownMonitor() as monitor:
            # Check for shutdown before starting
            monitor.raise_if_is_worker_shutdown()

            result = await otel_sync_to_async(
                _persist_column_cells_sync, thread_sensitive=False
            )(
                input.dataset_id,
                input.column_ids,
                input.data,
            )

        if result.get("status") == "FAILED":
            activity.logger.exception(
                f"persist_column_cells_activity failed: {result.get('error')}"
            )

        return PersistColumnCellsOutput(
            status=result["status"],
            cells_updated=result.get("cells_updated", 0),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"persist_column_cells_activity failed: {e}")
        return PersistColumnCellsOutput(
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def add_scenario_columns_activity(
    input: AddScenarioColumnsInput,
) -> None:
    """
    Activity wrapper for add_scenario_columns_task.

    This wraps the complete add_scenario_columns_task which handles:
    - Building generation payload from existing dataset context
    - Generating new column data using SyntheticDataAgent
    - Persisting cells to database
    - Updating column statuses

    Uses Heartbeater for automatic heartbeats during long-running generation.
    """
    from simulate.tasks.scenario_tasks import add_scenario_columns_task
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Starting add_scenario_columns_activity for dataset_id={input.dataset_id}, "
        f"scenario_id={input.scenario_id}, columns={len(input.columns_info)}"
    )

    try:
        # Usage pre-check
        try:
            from django.db import close_old_connections as _close_check

            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _close_check()
            _asc_scenario = Scenarios.objects.get(id=input.scenario_id)
            _asc_org_id = str(_asc_scenario.organization.id)
            usage_check = check_usage(
                _asc_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        async with Heartbeater() as heartbeater:
            heartbeater.details = ("generating_columns", len(input.columns_info))

            # Call the synchronous add_scenario_columns_task function
            # Use thread_sensitive=False to allow thread pool execution for I/O-bound work
            await otel_sync_to_async(add_scenario_columns_task, thread_sensitive=False)(
                input.dataset_id,
                input.scenario_id,
                input.columns_info,
                input.column_ids,
            )

        activity.logger.info(
            f"add_scenario_columns_activity completed for dataset_id={input.dataset_id}"
        )

    except Exception as e:
        activity.logger.exception(f"add_scenario_columns_activity failed: {e}")
        # The add_scenario_columns_task function already handles error cleanup
        # (updates column statuses to FAILED internally)
        raise


# =============================================================================
# Scenario Creation Activities
# =============================================================================


# Excluded column sources when copying dataset for scenarios
EXCLUDED_SCENARIO_SOURCES = [
    SourceChoices.EXPERIMENT.value,
    SourceChoices.EXPERIMENT_EVALUATION.value,
    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
    SourceChoices.EVALUATION.value,
    SourceChoices.EVALUATION_TAGS.value,
    SourceChoices.EVALUATION_REASON.value,
    SourceChoices.OPTIMISATION_EVALUATION.value,
    SourceChoices.OPTIMISATION_EVALUATION_TAGS.value,
]


def _copy_source_dataset(
    source_dataset: Dataset,
    scenario: Scenarios,
    user: User,
    mode: str,
) -> tuple[Dataset, dict, list, set]:
    """Copy source dataset with columns, rows, and cells.

    Returns:
        Tuple of (new_dataset, column_id_mapping, new_columns, existing_column_names)
    """
    import uuid as uuid_module

    new_dataset = Dataset.no_workspace_objects.create(
        id=uuid_module.uuid4(),
        name=f"Copy of {source_dataset.name}",
        organization=source_dataset.organization,
        workspace=scenario.workspace,
        model_type=source_dataset.model_type,
        column_order=(
            source_dataset.column_order.copy() if source_dataset.column_order else []
        ),
        column_config=(
            source_dataset.column_config.copy() if source_dataset.column_config else {}
        ),
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )

    # Copy columns (exclude evaluation columns)
    column_id_mapping = {}
    new_columns = []

    source_columns = Column.objects.filter(
        dataset=source_dataset, deleted=False
    ).exclude(source__in=EXCLUDED_SCENARIO_SOURCES)

    existing_column_names = {col.name for col in source_columns}

    for column in source_columns:
        new_column_id = uuid_module.uuid4()
        column_id_mapping[str(column.id)] = str(new_column_id)

        new_columns.append(
            Column(
                id=new_column_id,
                name=column.name,
                data_type=column.data_type,
                source=SourceChoices.OTHERS.value,
                dataset=new_dataset,
                deleted=False,
            )
        )

    return new_dataset, column_id_mapping, new_columns, existing_column_names


def _add_scenario_columns(
    new_dataset: Dataset,
    existing_column_names: set,
    agent_definition,
    mode: str,
) -> dict:
    """Add scenario-specific columns to the dataset.

    Returns:
        Dict mapping column names to their IDs.
    """
    import uuid as uuid_module

    scenario_columns_config = {
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

    new_scenario_columns = {}
    new_columns = []

    for col_name, col_config in scenario_columns_config.items():
        if col_name not in existing_column_names:
            new_column_id = uuid_module.uuid4()
            new_scenario_columns[col_name] = new_column_id

            metadata = {}
            if col_name == "persona":
                metadata = {"simulation_type": agent_definition.agent_type}

            new_columns.append(
                Column(
                    id=new_column_id,
                    name=col_name,
                    data_type=col_config["data_type"],
                    source=SourceChoices.OTHERS.value,
                    dataset=new_dataset,
                    deleted=False,
                    metadata=metadata,
                )
            )

    if new_columns:
        Column.objects.bulk_create(new_columns)

    return new_scenario_columns, scenario_columns_config


def _copy_rows_and_cells(
    source_dataset: Dataset,
    new_dataset: Dataset,
    column_id_mapping: dict,
) -> list:
    """Copy rows and cells from source to new dataset.

    Returns:
        List of new row IDs in order.
    """
    import uuid as uuid_module

    source_rows = Row.objects.filter(dataset=source_dataset, deleted=False)
    new_rows = []
    row_id_mapping = {}
    all_new_row_ids = []

    batch_size = 1000
    total_rows = source_rows.count()

    for i in range(0, total_rows, batch_size):
        batch_rows = source_rows[i : i + batch_size]

        for row in batch_rows:
            new_row_id = uuid_module.uuid4()
            row_id_mapping[str(row.id)] = str(new_row_id)
            all_new_row_ids.append(new_row_id)

            new_rows.append(
                Row(
                    id=new_row_id,
                    dataset=new_dataset,
                    order=row.order,
                    deleted=False,
                )
            )

        Row.objects.bulk_create(new_rows[-len(batch_rows) :])

        # Copy cells for this batch
        source_cells = Cell.objects.filter(row__in=batch_rows, deleted=False)
        new_cells = []
        for cell in source_cells:
            if (
                str(cell.column.id) in column_id_mapping
                and str(cell.row.id) in row_id_mapping
            ):
                new_cells.append(
                    Cell(
                        id=uuid_module.uuid4(),
                        row_id=row_id_mapping[str(cell.row.id)],
                        column_id=column_id_mapping[str(cell.column.id)],
                        value=cell.value,
                        dataset=new_dataset,
                        deleted=False,
                    )
                )

        Cell.objects.bulk_create(new_cells[-len(list(source_cells)) :])

    return all_new_row_ids


def _build_sda_payload(
    new_dataset: Dataset,
    agent_definition,
    mode: str,
) -> dict:
    """Build the payload for SyntheticDataAgent.generate_column_data.

    Returns:
        Dict with requirements, constraints, and schema.
    """
    try:
        from ee.agenthub.scenario_graph.persona_configurator import (
            PersonaConfigurator,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.persona_configurator", exc_info=True)
        return None

    property_dict = PersonaConfigurator.get_property_dict(mode)

    agent_name = getattr(agent_definition, "agent_name", "")
    agent_description = getattr(agent_definition, "description", "")
    agent_languages = getattr(agent_definition, "languages", [])
    is_inbound = getattr(agent_definition, "inbound", False)
    call_type_val = "Inbound" if is_inbound else "Outbound"
    interaction_term = "call" if mode == "voice" else "chat session"

    situation_instruction = (
        "Do not explicitly describe environmental details like traffic noise playing or label emotions directly. "
        "Instead, express the customer's situation through natural behavior and context that implies their state. "
        "Write in third-person."
        if mode == "voice"
        else "Do not describe environmental sounds. Focus on the context in which the user is texting. "
        "Include typos or short phrasing if appropriate for the situation. Write in third-person."
    )

    requirements = {
        "Dataset Name": new_dataset.name,
        "Dataset Description": (
            f"Scenario dataset for {agent_name}. "
            f"Agent Purpose: {agent_description}. "
            f"Supported Languages: {agent_languages}. "
            f"Call Type: {call_type_val}."
        ),
        "Objective": (
            f"Generate realistic persona, situation, and outcome for {agent_name} scenarios "
            f"that align with conversation branch context provided."
        ),
    }

    constraints = [
        {
            "field": "persona",
            "type": "json",
            "content": (
                f"Detailed customer persona profile for {agent_name}. "
                "For name always generate a realistic full name based on other characteristics."
            ),
            "property": property_dict,
        },
        {
            "field": "situation",
            "type": "text",
            "content": (
                f"Specific situation of the customer when they initiate a {interaction_term} "
                f"with agent: {agent_name}. Situation should be tightly linked to the customer persona "
                f"and naturally lead to the conversation branch described in the branch context. "
                f"{situation_instruction}"
            ),
            "property": {
                "min_length": 30,
                "max_length": 400,
            },
        },
        {
            "field": "outcome",
            "type": "text",
            "content": (
                "Create a specific outcome reflecting how the interaction resolves following the "
                "conversation branch described in the branch context. "
                "Write in third-person past tense, 2-4 sentences (45-90 words), "
                "describing the customer's final decision, the agent's key actions, and next steps."
            ),
            "property": {
                "min_length": 30,
                "max_length": 400,
            },
        },
    ]

    schema = {
        "persona": {"type": "json"},
        "situation": {"type": "text"},
        "outcome": {"type": "text"},
    }

    return {
        "requirements": requirements,
        "constraints": constraints,
        "schema": schema,
        "property_dict": property_dict,
    }


def _resolve_scenario_agent(scenario, source_type):
    """Return the agent_definition (real or prompt-source adapter) for a
    scenario. Mirrors the adapter pattern in `_create_graph_scenario_sync`
    and `_create_script_scenario_sync` so the dataset path can accept
    prompt sources too (TH-4891)."""
    if source_type == "prompt" and scenario.prompt_template:
        prompt_content = ""
        prompt_version = scenario.prompt_version
        if not prompt_version:
            prompt_version = scenario.prompt_template.all_executions.filter(
                is_default=True, deleted=False
            ).first()

        if prompt_version and prompt_version.prompt_config_snapshot:
            config_snapshot = prompt_version.prompt_config_snapshot
            config = (
                config_snapshot[0]
                if isinstance(config_snapshot, list)
                else config_snapshot
            )
            if isinstance(config, dict):
                messages = config.get("messages", [])
                formatted_messages = []
                for msg in messages:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text_parts = [
                            p.get("text", "")
                            for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        ]
                        content = "\n".join(text_parts)
                    if content:
                        formatted_messages.append(f"[{role}]: {content}")
                prompt_content = "\n\n".join(formatted_messages)

        agent_description = (
            prompt_content or scenario.prompt_template.description or ""
        )
        return types.SimpleNamespace(
            id=scenario.prompt_template.id,
            agent_name=scenario.prompt_template.name,
            description=agent_description,
            agent_type="text",
            languages=["en"],
            language="en",
            inbound=True,
            contact_number=None,
            organization=scenario.organization,
            workspace=scenario.workspace,
        )

    return scenario.agent_definition


def _create_dataset_scenario_sync(
    user_id: str,
    scenario_id: str,
    validated_data: dict,
) -> dict:
    """Synchronous implementation of dataset scenario creation.

    Flow:
    1. Copy source dataset (columns, rows, cells)
    2. Generate workflow graph from agent_definition
    3. Extract branches, build metadata
    4. Add 5 scenario columns (persona, situation, outcome, conversation_branch, branch_category)
    5. Generate scenario column values using SDA.generate_column_data with branch context
    6. Persist generated cells
    """
    import uuid as uuid_module
    from collections import defaultdict

    from django.db import close_old_connections, transaction
    from django.shortcuts import get_object_or_404

    try:
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
        from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
            SyntheticDataAgent,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.graph_generator", exc_info=True)
        return None

    try:
        close_old_connections()

        user = User.objects.get(id=user_id)
        scenario = Scenarios.objects.get(id=scenario_id)
        source_type = validated_data.get("source_type", "agent_definition")
        agent_definition = _resolve_scenario_agent(scenario, source_type)

        if not agent_definition:
            raise ValueError(
                "Either agent_definition or prompt_template is required on "
                "the scenario for dataset scenario creation"
            )

        # Usage pre-check
        _cds_org_id = None
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _cds_org_id = str(scenario.organization.id)
            usage_check = check_usage(
                _cds_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        # Get the source dataset
        source_dataset = get_object_or_404(
            Dataset,
            id=validated_data["dataset_id"],
            deleted=False,
            organization=get_current_organization() or user.organization,
        )

        # Determine simulation mode
        mode = "voice" if agent_definition.agent_type == "voice" else "chat"

        with transaction.atomic():
            # ========================================
            # PHASE 1: Copy source dataset
            # ========================================
            new_dataset, column_id_mapping, new_columns, existing_column_names = (
                _copy_source_dataset(source_dataset, scenario, user, mode)
            )

            # Bulk create the copied source columns
            if new_columns:
                Column.objects.bulk_create(new_columns)

            # ========================================
            # PHASE 2: Add 5 scenario columns
            # ========================================
            new_scenario_columns, scenario_columns_config = _add_scenario_columns(
                new_dataset, existing_column_names, agent_definition, mode
            )

            # Update column_order with new column IDs
            new_column_order = []
            new_column_config = {}

            if new_dataset.column_order:
                for old_col_id in new_dataset.column_order:
                    if old_col_id in column_id_mapping:
                        new_column_order.append(column_id_mapping[old_col_id])
                        if (
                            new_dataset.column_config
                            and old_col_id in new_dataset.column_config
                        ):
                            new_column_config[column_id_mapping[old_col_id]] = (
                                new_dataset.column_config[old_col_id]
                            )

            # Add scenario columns to column_order and column_config
            for col_name, col_id in new_scenario_columns.items():
                new_column_order.append(str(col_id))
                new_column_config[str(col_id)] = {
                    "name": col_name,
                    "type": scenario_columns_config[col_name]["data_type"],
                    "description": scenario_columns_config[col_name]["description"],
                }
                if col_name == "persona":
                    new_column_config[str(col_id)][
                        "simulation_type"
                    ] = agent_definition.agent_type

            new_dataset.column_order = new_column_order
            new_dataset.column_config = new_column_config
            new_dataset.save()

            # Copy rows and cells in batches
            all_new_row_ids = _copy_rows_and_cells(
                source_dataset, new_dataset, column_id_mapping
            )

        # ========================================
        # PHASE 2b: Link dataset to scenario early (so users can see it)
        # ========================================
        scenario.dataset = new_dataset
        scenario.source = f"Generating scenario from dataset: {new_dataset.name}"
        scenario.status = StatusType.PROCESSING.value
        scenario.save()

        logger.info(
            f"Dataset {new_dataset.id} created and linked to scenario {scenario_id}. "
            f"Users can now see the dataset with RUNNING status."
        )

        # ========================================
        # PHASE 3: Generate workflow graph
        # ========================================
        graph_generator = ConversationGraphGenerator(
            scenario=scenario,
            agent_definition=agent_definition,
        )
        scenario_graph = graph_generator.generate_graph(save_to_db=True)

        if not scenario_graph:
            raise ValueError("Failed to generate workflow graph from agent_definition")

        # ========================================
        # PHASE 4: Extract branches and build metadata
        # ========================================
        branches = graph_generator.get_branches(graph_id=str(scenario_graph.id))
        if not branches:
            raise ValueError("No branches extracted from the generated graph")

        # Build detailed branch metadata
        detailed_branches_metadata = []
        for branch in branches:
            detailed_branch = graph_generator.get_branch_with_messages_and_prompts(
                branch, str(scenario_graph.id)
            )
            # Build metadata string for each branch
            path_nodes = detailed_branch.get("path", [])
            branch_name = (
                " -> ".join(path_nodes)
                if path_nodes
                else detailed_branch.get("end_node", "unknown")
            )
            start_node = detailed_branch.get("start_node", "unknown")
            end_node = detailed_branch.get("end_node", "unknown")

            # Build conversation flow description
            flow_parts = []
            for node_idx, node_info in enumerate(
                detailed_branch.get("detailedPath", [])
            ):
                node_name_val = node_info.get("name", "unknown")
                node_type = node_info.get("type", "conversation")
                prompt = node_info.get("prompt", "")
                flow_parts.append(f"Step {node_idx + 1}: {node_name_val} ({node_type})")
                if prompt:
                    flow_parts.append(f"  Prompt: {prompt}")

            conversation_flow = "\n".join(flow_parts)

            metadata_lines = [
                "Conversation Branch Information:",
                f"- Branch Name: {branch_name}",
                f"- Start Node: {start_node}",
                f"- End Node: {end_node}",
                f"- Conversation Flow:\n{conversation_flow}",
            ]

            detailed_branches_metadata.append(
                {
                    "branch_name": branch_name,
                    "metadata_string": "\n".join(metadata_lines),
                }
            )

        # ========================================
        # PHASE 5: Build reference data and generate scenario columns
        # ========================================
        num_rows = len(all_new_row_ids)
        num_branches = len(detailed_branches_metadata)

        # Distribute branches evenly across rows
        branch_assignments = []  # index into detailed_branches_metadata per row
        rows_per_branch = num_rows // num_branches if num_branches > 0 else 1
        remainder = num_rows % num_branches if num_branches > 0 else 0
        for branch_idx in range(num_branches):
            count = rows_per_branch + (1 if branch_idx < remainder else 0)
            branch_assignments.extend([branch_idx] * count)

        # Build per-row branch metadata strings
        per_row_branch_metadatas = [
            detailed_branches_metadata[branch_assignments[i]]["metadata_string"]
            for i in range(num_rows)
        ]

        # Build per-row conversation_branch and branch_category values
        per_row_branch_names = [
            detailed_branches_metadata[branch_assignments[i]]["branch_name"]
            for i in range(num_rows)
        ]

        # Build reference data from existing copied cells
        new_rows_qs = Row.objects.filter(dataset=new_dataset, deleted=False).order_by(
            "order"
        )
        ordered_new_row_ids = list(new_rows_qs.values_list("id", flat=True))

        # Get all cells for copied columns (not scenario columns)
        # Use exclude() in the query instead of filtering in Python for efficiency
        scenario_col_ids = list(new_scenario_columns.values())
        copied_column_ids = list(
            Column.objects.filter(dataset=new_dataset, deleted=False)
            .exclude(id__in=scenario_col_ids)
            .values_list("id", flat=True)
        )

        cells = (
            Cell.objects.filter(
                dataset=new_dataset,
                row__id__in=ordered_new_row_ids,
                column__id__in=copied_column_ids,
            )
            .select_related("column")
            .values("row_id", "column__name", "value")
        )
        cells_by_row = defaultdict(dict)
        for cell in cells:
            cells_by_row[cell["row_id"]][cell["column__name"]] = cell["value"]

        reference_rows = []
        for row_id in ordered_new_row_ids:
            reference_rows.append(dict(cells_by_row.get(row_id, {})))

        # Build SDA payload for the 3 LLM-generated columns
        # (conversation_branch and branch_category are filled deterministically)
        sda_payload_data = _build_sda_payload(new_dataset, agent_definition, mode)
        base_payload = {
            "requirements": sda_payload_data["requirements"],
            "constraints": sda_payload_data["constraints"],
            "schema": sda_payload_data["schema"],
        }
        property_dict = sda_payload_data["property_dict"]

        # ========================================
        # PHASE 5: Mark scenario columns as RUNNING and prepare parallel generation
        # ========================================
        # Get scenario columns for cell creation and mark them as RUNNING
        scenario_columns = list(
            Column.objects.filter(
                dataset=new_dataset,
                name__in=list(scenario_columns_config.keys()),
                deleted=False,
            )
        )
        scenario_columns_by_name = {col.name: col for col in scenario_columns}

        # Mark columns as RUNNING using the fetched queryset
        Column.objects.filter(id__in=[col.id for col in scenario_columns]).update(
            status=StatusType.RUNNING.value
        )

        # Create placeholder cells with RUNNING status for all scenario columns
        # This makes cells visible to users immediately
        placeholder_cells = []
        for row_id in ordered_new_row_ids:
            for col_name in [
                "persona",
                "situation",
                "outcome",
                "conversation_branch",
                "branch_category",
            ]:
                if col_name in scenario_columns_by_name:
                    placeholder_cells.append(
                        Cell(
                            id=uuid_module.uuid4(),
                            row_id=row_id,
                            column=scenario_columns_by_name[col_name],
                            value="",
                            dataset=new_dataset,
                            deleted=False,
                            status=CellStatus.RUNNING.value,
                        )
                    )

        if placeholder_cells:
            Cell.objects.bulk_create(placeholder_cells, batch_size=500)
            logger.info(
                f"Created {len(placeholder_cells)} placeholder cells with RUNNING status"
            )

        # Pre-fetch all placeholder cells once (single query instead of N per-branch queries)
        all_scenario_cells = list(
            Cell.objects.filter(
                dataset=new_dataset,
                row_id__in=ordered_new_row_ids,
                column__name__in=[
                    "persona",
                    "situation",
                    "outcome",
                    "conversation_branch",
                ],
            ).select_related("column")
        )
        cell_lookup = {
            (str(cell.row_id), cell.column.name): cell for cell in all_scenario_cells
        }

        # Group rows by branch for parallel processing
        branch_row_groups = defaultdict(list)
        for row_idx, branch_idx in enumerate(branch_assignments):
            branch_row_groups[branch_idx].append(row_idx)

        logger.info(
            f"Generating scenario columns for {num_rows} rows across {len(branch_row_groups)} branches in parallel"
        )

        # Create SyntheticDataAgent once for all branches (reuse across parallel generations)
        shared_agent = SyntheticDataAgent()

        # Wrap all async operations in a single async function
        async def run_async_generation():
            """Run all async generation and categorization operations."""
            from asgiref.sync import sync_to_async

            async def generate_branch(
                branch_idx: int, row_indices: List[int]
            ) -> Tuple[int, pd.DataFrame]:
                """Generate data for a branch and persist cells immediately for real-time updates."""
                try:
                    # Build payload for this branch's rows only
                    branch_reference_rows = [reference_rows[i] for i in row_indices]
                    branch_metadata = [per_row_branch_metadatas[i] for i in row_indices]

                    branch_payload = {
                        **base_payload,
                        "reference_data": branch_reference_rows,
                        "batch_size": len(branch_reference_rows),
                    }

                    # Generate data for this branch using shared agent
                    branch_df = await shared_agent.generate_column_data(
                        branch_payload, branch_metadatas=branch_metadata
                    )

                    # Validate and fix personas using PersonaConfigurator
                    for df_idx in range(len(branch_df)):
                        persona_val = branch_df.iloc[df_idx].get("persona", {})
                        validated_persona = (
                            PersonaConfigurator.validate_and_correct_persona(
                                persona_data=persona_val,
                                mode=mode,
                                valid_values=property_dict,
                            )
                        )
                        branch_df.at[df_idx, "persona"] = validated_persona

                    # Update cells using pre-fetched lookup (no per-branch DB query)
                    cells_to_update = []
                    for df_idx, row_idx in enumerate(row_indices):
                        row_id = ordered_new_row_ids[row_idx]

                        # Update persona, situation, outcome cells
                        for col_name in ["persona", "situation", "outcome"]:
                            if col_name not in scenario_columns_by_name:
                                continue
                            cell_key = (str(row_id), col_name)
                            if cell_key in cell_lookup:
                                cell = cell_lookup[cell_key]
                                value = ""
                                if df_idx < len(branch_df):
                                    value = branch_df.iloc[df_idx].get(col_name, "")
                                    if col_name == "persona" and isinstance(
                                        value, dict
                                    ):
                                        value = json.dumps(value)
                                cell.value = value
                                cell.status = CellStatus.PASS.value
                                cells_to_update.append(cell)

                        # Update conversation_branch cell (deterministic)
                        if "conversation_branch" in scenario_columns_by_name:
                            cell_key = (str(row_id), "conversation_branch")
                            if cell_key in cell_lookup:
                                cell = cell_lookup[cell_key]
                                cell.value = per_row_branch_names[row_idx]
                                cell.status = CellStatus.PASS.value
                                cells_to_update.append(cell)

                    # Persist this branch's cells immediately for real-time updates
                    if cells_to_update:
                        await sync_to_async(
                            Cell.objects.bulk_update, thread_sensitive=False
                        )(cells_to_update, ["value", "status"], batch_size=500)

                    logger.info(
                        f"Branch {branch_idx + 1}/{len(branch_row_groups)}: Generated and persisted {len(row_indices)} rows"
                    )

                    return branch_idx, branch_df

                except Exception as e:
                    logger.exception(
                        f"Error generating branch {branch_idx} with {len(row_indices)} rows: {e}"
                    )
                    # Return empty dataframe on failure
                    return branch_idx, pd.DataFrame()

            # Run all branch generations in parallel
            branch_tasks = [
                generate_branch(branch_idx, row_indices)
                for branch_idx, row_indices in branch_row_groups.items()
            ]
            branch_results = await asyncio.gather(*branch_tasks, return_exceptions=True)

            # Combine all branch dataframes in order
            branch_dataframes = {}
            for result in branch_results:
                if isinstance(result, Exception):
                    logger.error(f"Branch generation failed: {result}")
                    continue
                branch_idx, branch_df = result
                branch_dataframes[branch_idx] = branch_df

            # Build combined synthetic_df for branch categorization
            # Order rows by original row index
            all_rows_data = []
            for row_idx in range(num_rows):
                branch_idx = branch_assignments[row_idx]
                if branch_idx in branch_dataframes:
                    branch_df = branch_dataframes[branch_idx]
                    # Find this row's position in the branch dataframe
                    row_indices = branch_row_groups[branch_idx]
                    df_idx = row_indices.index(row_idx)
                    if df_idx < len(branch_df):
                        all_rows_data.append(branch_df.iloc[df_idx].to_dict())
                    else:
                        all_rows_data.append({})
                else:
                    all_rows_data.append({})

            synthetic_df = pd.DataFrame(all_rows_data)

            # ========================================
            # PHASE 5b: Generate branch categories
            # ========================================
            try:
                from ee.agenthub.scenario_graph.prompt import (
                    UNIFIED_CATEGORY_PROMPT,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.prompt", exc_info=True)
                return None

            # Build branch to situations mapping from generated data
            branch_to_situations = {}
            for idx in range(len(synthetic_df)):
                branch_name = (
                    per_row_branch_names[idx] if idx < len(per_row_branch_names) else ""
                )
                situation = synthetic_df.iloc[idx].get("situation", "")
                if branch_name:
                    if branch_name not in branch_to_situations:
                        branch_to_situations[branch_name] = []
                    if situation:
                        branch_to_situations[branch_name].append(situation)

            # Categorize each branch using LLM (in parallel)
            branch_to_category = {}
            if branch_to_situations:
                llm = LLM(
                    model_name="vertex_ai/gemini-2.5-pro",
                    temperature=0.3,
                    max_tokens=400,
                    provider="vertex_ai",
                    api_key=None,
                )

                async def categorize_single_branch(
                    branch_name: str, situations: list
                ) -> tuple:
                    """Categorize a single branch asynchronously."""
                    try:
                        prompt = UNIFIED_CATEGORY_PROMPT.format(
                            branch=branch_name,
                            situations=situations[:5],  # Limit to first 5 situations
                        )
                        messages = [{"role": "user", "content": prompt}]
                        response = await llm._get_completion_content_async(messages)
                        category = response.strip()
                        return branch_name, category
                    except Exception as e:
                        logger.warning(f"Error categorizing branch {branch_name}: {e}")
                        return branch_name, ""

                # Run all categorizations in parallel
                categorization_tasks = [
                    categorize_single_branch(branch_name, situations)
                    for branch_name, situations in branch_to_situations.items()
                ]
                categorization_results = await asyncio.gather(
                    *categorization_tasks, return_exceptions=True
                )

                # Process results
                for result in categorization_results:
                    if isinstance(result, Exception):
                        logger.error(f"Branch categorization failed: {result}")
                        continue
                    branch_name, category = result
                    branch_to_category[branch_name] = category

            # Build per-row branch categories
            per_row_branch_categories = [
                branch_to_category.get(per_row_branch_names[i], "")
                for i in range(num_rows)
            ]

            return per_row_branch_categories

        # Execute all async operations
        per_row_branch_categories = asyncio.run(run_async_generation())

        # ========================================
        # PHASE 6: Update branch_category cells (depends on categorization LLM results)
        # ========================================
        if "branch_category" in scenario_columns_by_name:
            category_cells = list(
                Cell.objects.filter(
                    dataset=new_dataset,
                    column=scenario_columns_by_name["branch_category"],
                    row_id__in=ordered_new_row_ids,
                )
            )
            category_cell_lookup = {str(cell.row_id): cell for cell in category_cells}

            cells_to_update = []
            for row_idx, row_id in enumerate(ordered_new_row_ids):
                cell_key = str(row_id)
                if cell_key in category_cell_lookup:
                    cell = category_cell_lookup[cell_key]
                    cell.value = (
                        per_row_branch_categories[row_idx]
                        if row_idx < len(per_row_branch_categories)
                        else ""
                    )
                    cell.status = CellStatus.PASS.value
                    cells_to_update.append(cell)

            if cells_to_update:
                Cell.objects.bulk_update(
                    cells_to_update, ["value", "status"], batch_size=500
                )

        # Update column statuses to COMPLETED
        Column.objects.filter(
            dataset=new_dataset,
            name__in=list(scenario_columns_config.keys()),
        ).update(status=StatusType.COMPLETED.value)

        logger.info(
            f"Completed scenario column generation: {len(branch_row_groups)} branches, {num_rows} total rows"
        )

        # ========================================
        # PHASE 7: Update scenario to COMPLETED
        # ========================================
        # Dataset already linked in PHASE 2b, just update status
        scenario.source = f"Created from dataset: {new_dataset.name}"
        scenario.status = StatusType.COMPLETED.value

        # Store persona_ids in metadata if provided
        persona_ids = validated_data.get("personas", [])
        if persona_ids:
            current_metadata = scenario.metadata if scenario.metadata else {}
            if isinstance(current_metadata, str):
                try:
                    current_metadata = json.loads(current_metadata)
                except Exception:
                    current_metadata = {}
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
            scenario.metadata = current_metadata

        scenario.save()

        # Usage emit
        try:
            if _cds_org_id:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _total_cost = graph_generator.sda.llm.cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(_total_cost)
                emit(
                    UsageEvent(
                        org_id=_cds_org_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_dataset_scenario_creation",
                            "source_id": str(scenario_id),
                            "raw_cost_usd": str(_total_cost),
                            **token_usage_properties(
                                graph_generator.sda.llm.token_usage
                            ),
                        },
                    )
                )
        except Exception:
            pass

        return {
            "scenario_id": scenario_id,
            "dataset_id": str(new_dataset.id),
            "status": "COMPLETED",
        }

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def create_dataset_scenario_activity(
    input: CreateDatasetScenarioWorkflowInput,
) -> CreateDatasetScenarioWorkflowOutput:
    """
    Create a dataset-based scenario.

    This is a direct port of create_dataset_scenario_background_task.
    Copies source dataset with columns/rows/cells and creates scenario columns.
    Uses Heartbeater for automatic heartbeats during long-running database operations.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Creating dataset scenario for scenario_id={input.scenario_id}"
    )

    async with Heartbeater():
        result = await otel_sync_to_async(
            _create_dataset_scenario_sync, thread_sensitive=False
        )(
            input.user_id,
            input.scenario_id,
            input.validated_data,
        )

    if result.get("status") == "FAILED":
        activity.logger.exception(
            f"Failed to create dataset scenario: {result.get('error')}"
        )

    activity.logger.info(
        f"Dataset scenario created successfully: scenario_id={input.scenario_id}, "
        f"dataset_id={result.get('dataset_id')}"
    )

    return CreateDatasetScenarioWorkflowOutput(
        scenario_id=result["scenario_id"],
        dataset_id=result.get("dataset_id"),
        status=result["status"],
        error=result.get("error"),
    )


def _create_script_scenario_sync(
    scenario_id: str,
    validated_data: dict,
) -> dict:
    """Synchronous implementation of script scenario creation."""
    import json

    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
        return None
    from model_hub.models.choices import StatusType
    from simulate.models import Scenarios
    from simulate.views.scenarios import convert_personas_to_property_list

    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        # Usage pre-check
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _org_id = str(scenario.organization.id)
            usage_check = check_usage(
                _org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        no_of_rows = validated_data.get("no_of_rows", 20)
        script_url = validated_data.get("script_url")
        agent_definition_id = validated_data.get("agent_definition_id")
        persona_ids = validated_data.get("personas", [])
        custom_columns = validated_data.get("custom_columns", [])

        script_content = ""

        # Convert persona IDs to property_list
        property_list = convert_personas_to_property_list(persona_ids)

        # Update scenario source and metadata with persona_ids
        scenario.source = script_content

        # Preserve existing metadata (e.g., agent_definition_version_id, custom_instruction)
        current_metadata = scenario.metadata if scenario.metadata else {}
        if isinstance(current_metadata, str):
            try:
                current_metadata = json.loads(current_metadata)
            except Exception:
                current_metadata = {}

        current_metadata["script_url"] = script_url
        if persona_ids:
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
        scenario.metadata = current_metadata
        scenario.save()

        # Handle graph generation or use provided graph data
        generated_graph_data = None
        if agent_definition_id:
            # Generate graph using ConversationGraphGenerator
            try:
                graph_generator = ConversationGraphGenerator(
                    agent_definition_id=str(agent_definition_id),
                    scenario=scenario,
                    script_url=script_url,
                )
                generated_graph_data = graph_generator.generate_graph(save_to_db=True)

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to generate graph: {str(e)}")

        enhanced_agent = EnhancedScenariosAgent(
            str(agent_definition_id),
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
        )
        s, d = enhanced_agent.run(
            name=scenario.name,
            description=scenario.description,
            user_requirements={},
            graph_id=str(generated_graph_data.id) if generated_graph_data else None,
            property_list=property_list,
        )

        try:
            if _org_id:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _cost = enhanced_agent.llm.cost.get("total_cost", 0)
                emit(
                    UsageEvent(
                        org_id=_org_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=BillingConfig.get().calculate_ai_credits(_cost),
                        properties={
                            "source": "simulate_dataset_scenario",
                            "source_id": scenario_id,
                            "raw_cost_usd": str(_cost),
                            **token_usage_properties(enhanced_agent.llm.token_usage),
                        },
                    )
                )
        except Exception:
            pass

        scenario.status = StatusType.COMPLETED.value
        scenario.dataset = d
        scenario.save()

        return {
            "scenario_id": scenario_id,
            "dataset_id": str(d.id) if d else None,
            "status": "COMPLETED",
        }

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def create_script_scenario_activity(
    input: CreateScriptScenarioWorkflowInput,
) -> CreateScriptScenarioWorkflowOutput:
    """
    Create a script-based scenario.

    This is a direct port of create_script_scenario_background_task.
    Generates graph from script URL and creates dataset using EnhancedScenariosAgent.
    Uses Heartbeater for automatic heartbeats during long-running graph generation.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Creating script scenario for scenario_id={input.scenario_id}"
    )

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _create_script_scenario_sync, thread_sensitive=False
            )(input.scenario_id, input.validated_data)

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Script scenario created successfully: scenario_id={input.scenario_id}, "
                f"dataset_id={result.get('dataset_id')}"
            )
        else:
            activity.logger.error(
                f"Failed to create script scenario: {result.get('error')}"
            )

        return CreateScriptScenarioWorkflowOutput(
            scenario_id=result["scenario_id"],
            dataset_id=result.get("dataset_id"),
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to create script scenario: {e}")
        return CreateScriptScenarioWorkflowOutput(
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )


def propose_use_cases_covering_workflows(
    agent_definition: str,
    branches: List[Dict[str, Any]],
    llm,
    *,
    max_use_cases: int = 12,
    min_use_cases: int = 6,
    seed: Optional[int] = None,
    skip_coverage_patch: bool = False,
) -> List[Dict[str, Any]]:
    """
    Generate a *generic* list of use cases (test scenarios) such that every workflow edge/branch
    in the provided graph is covered by at least one use case.

    Inputs:
    - agent_definition: free-form text that defines the agent (persona, capabilities, policies)
    - llm: a caller you provide. Must support either:
        A) llm(messages, model=..., seed=..., temperature=...) -> str
        OR
        B) llm.chat.completions.create(model=..., messages=..., ...) -> response with .choices[0].message.content

    Output:
    A list of dicts like:
        {
        "use_case_id": "UC-01",
        "title": "...",
        "user_intent": "...",
        "setup_context": "...",
        "sample_user_utterances": ["...", "..."],
        "expected_paths": [
            {"from": "start", "to": "reschedule_cancel"},
            {"from": "reschedule_cancel", "to": "reschedule"},
            ...
        ],
        "coverage_notes": "...",
        "tags": ["reschedule", "handoff", ...]
        }

    Notes:
    - This function is intentionally generic; it does not hardcode any clinic/appointment logic.
    - It asks an LLM to (1) extract branch conditions from edges and (2) propose minimal
        use cases that cover all edges, including global nodes, transfers, and hangups.
    """

    graph_brief = {
        "branches": branches,
    }

    # ---- 2) LLM prompt: produce use cases that cover ALL branches ----
    system = (
        "You are a senior conversation designer and test engineer.\n"
        "Your job: create a compact set of USE CASES (test scenarios) that collectively cover\n"
        "EVERY branch (edge) in the workflow graph.\n"
        "A 'use case' can cover multiple branches and multiple flows; branches may appear in multiple use cases.\n"
        "Do not invent new nodes/edges. Use the provided branches exactly.\n"
        "Ensure global nodes are triggerable by at least one use case.\n"
        "Return STRICT JSON only.\n"
    )

    # Build a JSON-serializable summary of the AgentDefinition so json.dumps() won't fail
    try:
        agent_def_summary = {
            "id": str(getattr(agent_definition, "id", "")),
            "agent_name": getattr(agent_definition, "agent_name", ""),
            "description": getattr(agent_definition, "description", ""),
            "languages": getattr(agent_definition, "languages", []),
            "inbound": getattr(agent_definition, "inbound", False),
            "agent_type": getattr(agent_definition, "agent_type", ""),
        }
    except Exception:
        # Fallback to a string representation if something unexpected is present
        agent_def_summary = {"agent_definition": str(agent_definition)}

    user = {
        "task": "Generate use cases that cover all workflow branches.",
        "requirements": {
            "coverage": "All branches (edges) must be covered at least once across the returned use cases.",
            "use_case_count": {"min": min_use_cases, "max": max_use_cases},
            "format": {
                "type": "array",
                "items": {
                    "use_case_id": "string like UC-01",
                    "title": "short",
                    "user_intent": "what the caller wants",
                    "setup_context": "optional context / constraints",
                    "sample_user_utterances": "array of 2-5 utterances",
                    "expected_paths": "array of {from,to} edges in order",
                    "coverage_notes": "why this covers certain branches",
                    "tags": "array of strings",
                },
            },
            "edge_rules": [
                "expected_paths must be valid edges from the branch list.",
                "You may include multiple alternative expected_paths ONLY if clearly labeled as variants.",
                "At least one use case must trigger each global node (if any) and reach its tool edge (if present).",
            ],
        },
        "inputs": {
            "agent_definition": agent_def_summary,
            "workflow_graph_brief": graph_brief,
        },
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]

    # ---- 3) Call the provided LLM ----
    def _call_llm(msgs: list[str]) -> str:
        try:
            resp = llm._get_completion_content(msgs)

            if not resp or not resp.strip():
                raise ValueError("LLM returned empty response")

            return resp.strip()

        except Exception as e:
            activity.logger.exception("LLM call failed")
            raise RuntimeError(f"LLM call failed before JSON parsing: {str(e)}") from e

    raw = _call_llm(messages)

    # Some LLMs may wrap JSON object; normalize to list if needed.
    try:
        parsed = json.loads(raw)
    except:
        try:
            parsed = json_repair.loads(raw)
        except Exception as e:
            raise ValueError(f"LLM did not return valid JSON: {e}")

    if isinstance(parsed, dict) and "use_cases" in parsed:
        parsed = parsed["use_cases"]

    if not isinstance(parsed, list):
        raise ValueError("LLM did not return a JSON array of use cases.")

    if skip_coverage_patch:
        return parsed

    # ---- 4) (Optional but useful) Validate edge coverage ----
    all_edges = {(b["start_node"], b["end_node"]) for b in branches}
    covered = set()
    for uc in parsed:
        paths = uc.get("expected_paths", [])
        # allow variant structure but keep simple
        if isinstance(paths, list):
            for p in paths:
                if isinstance(p, dict) and "from" in p and "to" in p:
                    covered.add((p["from"], p["to"]))

    missing = sorted(list(all_edges - covered))
    if missing:
        # If missing, ask the LLM for a patch use case set that only covers missing edges.
        patch_user = {
            "task": "Some workflow branches are still uncovered. Create the smallest set of additional use cases to cover ONLY these missing branches.",
            "missing_edges": [{"from": f, "to": t} for (f, t) in missing],
            "available_branches": branches,
            "output_format": "same as before; return JSON array only",
        }
        patch_msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(patch_user, ensure_ascii=False)},
        ]
        patch_raw = _call_llm(patch_msgs)
        try:
            patch = json.loads(patch_raw)
        except Exception as e:
            try:
                patch = json_repair.loads(patch_raw)
            except Exception as e:
                raise ValueError(f"Failed to parse patch JSON: {e}")

        if isinstance(patch, dict) and "use_cases" in patch:
            patch = patch["use_cases"]
        if isinstance(patch, list):
            parsed.extend(patch)

    return parsed


def _create_graph_scenario_sync(
    scenario_id: str,
    validated_data: dict,
) -> dict:
    """Synchronous implementation of graph scenario creation."""
    import json

    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
        from ee.agenthub.scenario_graph.prompt import (
            USER_INTENT_PROMPT,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
        return None
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.utils.model_config import ModelConfigs
    from model_hub.models.choices import StatusType
    from simulate.models import (
        AgentDefinition,
        Scenarios,
    )
    from simulate.models.scenario_graph import ScenarioGraph
    from simulate.views.scenarios import convert_personas_to_property_list

    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        # Usage pre-check
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _org_id = str(scenario.organization.id)
            usage_check = check_usage(
                _org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        no_of_rows = validated_data.get("no_of_rows", 20)
        persona_ids = validated_data.get("personas", [])
        custom_columns = validated_data.get("custom_columns", [])
        transcripts = validated_data.get("transcripts", [])
        # Convert persona IDs to property_list
        property_list = convert_personas_to_property_list(persona_ids)

        # Build agent_definition object — real DB lookup or adapter for prompt sources
        if source_type == "prompt" and scenario.prompt_template:
            # Extract full prompt content from prompt_version config for graph generation
            prompt_content = ""
            prompt_version = scenario.prompt_version
            if not prompt_version:
                # Fallback to default version from template
                prompt_version = scenario.prompt_template.all_executions.filter(
                    is_default=True, deleted=False
                ).first()

            if prompt_version and prompt_version.prompt_config_snapshot:
                config_snapshot = prompt_version.prompt_config_snapshot
                # Handle both list and dict formats
                config = (
                    config_snapshot[0]
                    if isinstance(config_snapshot, list)
                    else config_snapshot
                )
                if isinstance(config, dict):
                    messages = config.get("messages", [])
                    # Format all messages into a readable prompt description
                    formatted_messages = []
                    for msg in messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Handle multimodal content - extract text parts
                            text_parts = [
                                p.get("text", "")
                                for p in content
                                if isinstance(p, dict) and p.get("type") == "text"
                            ]
                            content = "\n".join(text_parts)
                        if content:
                            formatted_messages.append(f"[{role}]: {content}")
                    prompt_content = "\n\n".join(formatted_messages)

            # Use full prompt content if available, otherwise fall back to description
            agent_description = (
                prompt_content or scenario.prompt_template.description or ""
            )

            agent_definition = types.SimpleNamespace(
                id=scenario.prompt_template.id,
                agent_name=scenario.prompt_template.name,
                description=agent_description,
                agent_type="text",
                languages=["en"],
                language="en",
                inbound=True,
                contact_number=None,
                organization=scenario.organization,
                workspace=scenario.workspace,
            )
        else:
            agent_definition = AgentDefinition.no_workspace_objects.get(
                id=agent_definition_id
            )

        # Update scenario source
        scenario.source = "Graph-based scenario"
        scenario.save()

        scenario_graph = None

        # Handle graph generation or use provided graph data
        if generate_graph:
            # Generate graph using ConversationGraphGenerator
            try:
                graph_generator = ConversationGraphGenerator(
                    scenario=scenario,
                    agent_definition=agent_definition,
                )
                scenario_graph = graph_generator.generate_graph(save_to_db=True)

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to generate graph: {str(e)}")

        elif graph_data:
            # Use provided graph data
            try:
                # Create ScenarioGraph with provided data
                scenario_graph = ScenarioGraph.objects.create(
                    scenario=scenario,
                    name=f"{scenario.name} - Graph",
                    description=f"Graph for {scenario.name}",
                    organization=scenario.organization,
                    workspace=scenario.workspace,
                    graph_config={"graph_data": graph_data, "source": "user_provided"},
                )

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to save graph data: {str(e)}")

        enhanced_agent = EnhancedScenariosAgent(
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
            agent_definition=agent_definition,
        )

        agent_description = getattr(agent_definition, "description", "")

        def get_user_intent(transcript: str, agent_description: str) -> str:
            """Extract user intent from the transcript using LLM.

            This method is designed to be called in parallel via ThreadPoolExecutor.
            Thread-safety: Creates thread-local LLM instance.
            """
            try:
                # Create thread-local LLM (shared self.llm is not thread-safe)

                prompt = USER_INTENT_PROMPT.format(
                    transcript=transcript, agent_definition=agent_description
                )
                llm_config = ModelConfigs.VERTEX_GEMINI_2_5_PRO
                llm = LLM(
                    model_name=llm_config.model_name,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                    provider=llm_config.provider,
                )

                messages = [{"role": "user", "content": prompt}]
                response = llm._get_completion_content(messages)
                intent = response.strip()
                # logger.debug(f"Generated user intent for transcript: {transcript}: {intent}")
                return intent
            except Exception as e:
                # logger.warning(f"Error generating user intent for transcript: {transcript}: {e}")
                return "miscellaneous"

        intent_dict = {}
        if transcripts:
            for key, transcript_dict in transcripts.items():
                intent = get_user_intent(
                    transcript_dict["transcript"], agent_description
                )
                intent_dict[key] = intent

        # If no transcripts provided, propose use cases to cover all branches
        else:
            branches = None
            graph_id = str(scenario_graph.id) if scenario_graph else None
            if graph_id:
                branches = graph_generator.get_branches(graph_id=graph_id)

            if branches:
                try:
                    # Use Flash model for intent proposal (classification task)
                    llm_config = ModelConfigs.VERTEX_GEMINI_2_5_FLASH
                    llm = LLM(
                        model_name=llm_config.model_name,
                        temperature=llm_config.temperature,
                        max_tokens=llm_config.max_tokens,
                        provider=llm_config.provider,
                    )
                    intents = propose_use_cases_covering_workflows(
                        agent_definition=agent_description,
                        branches=branches,
                        llm=llm,
                        skip_coverage_patch=no_of_rows <= 20,
                    )
                    # convert intents to intent_dict
                    for intent in intents:
                        use_case_id = intent.get("use_case_id")
                        user_intent = intent.get("user_intent", "miscellaneous")
                        intent_dict[use_case_id] = user_intent
                except Exception as e:
                    activity.logger.exception("Error proposing use cases:", e)

        s, d = enhanced_agent.run(
            name=scenario.name,
            description=scenario.description,
            user_requirements={},
            graph_id=str(scenario_graph.id) if scenario_graph else None,
            property_list=property_list,
            intent_dict=intent_dict,
        )

        try:
            if _org_id:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _cost = enhanced_agent.llm.cost.get("total_cost", 0)
                emit(
                    UsageEvent(
                        org_id=_org_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=BillingConfig.get().calculate_ai_credits(_cost),
                        properties={
                            "source": "simulate_script_scenario",
                            "source_id": scenario_id,
                            "raw_cost_usd": str(_cost),
                            **token_usage_properties(enhanced_agent.llm.token_usage),
                        },
                    )
                )
        except Exception:
            pass

        scenario.status = StatusType.COMPLETED.value
        scenario.dataset = d

        # Store persona_ids in metadata if provided, preserving existing metadata
        if persona_ids:
            current_metadata = scenario.metadata if scenario.metadata else {}
            if isinstance(current_metadata, str):
                try:
                    current_metadata = json.loads(current_metadata)
                except Exception:
                    current_metadata = {}
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
            scenario.metadata = current_metadata

        scenario.save()

        return {
            "scenario_id": scenario_id,
            "dataset_id": str(d.id) if d else None,
            "status": "COMPLETED",
        }

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


# =============================================================================
# JSON Serialization Helper
# =============================================================================


def _ensure_json_serializable(obj: Any, _depth: int = 0, _max_depth: int = 20) -> Any:
    """Recursively convert an object to ensure it's JSON serializable.

    Handles common cases:
    - None, str, int, float, bool -> as-is
    - dict -> recursively process keys (convert to str) and values
    - list/tuple -> recursively process elements
    - Objects with __dict__ -> convert to dict recursively
    - Other objects -> convert to str
    """
    if _depth >= _max_depth:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, dict):
        return {
            str(k) if k is not None else "null": _ensure_json_serializable(
                v, _depth + 1, _max_depth
            )
            for k, v in obj.items()
        }
    elif isinstance(obj, (list, tuple)):
        return [_ensure_json_serializable(item, _depth + 1, _max_depth) for item in obj]
    elif hasattr(obj, "__dict__"):
        return _ensure_json_serializable(vars(obj), _depth + 1, _max_depth)
    else:
        return str(obj)


# =============================================================================
# Graph Scenario Sub-Activities (v2 - multi-activity workflow)
# =============================================================================


@activity.defn
async def setup_graph_scenario_activity(
    input: SetupGraphScenarioInput,
) -> SetupGraphScenarioOutput:
    """
    Setup activity for graph scenario creation.

    Step 1: Load scenario, build agent definition, generate/save graph.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Setting up graph scenario for scenario_id={input.scenario_id}"
    )

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _setup_graph_scenario_sync, thread_sensitive=False
            )(input.scenario_id, input.validated_data)

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Graph scenario setup completed: scenario_id={input.scenario_id}, "
                f"graph_id={result.get('graph_id')}"
            )
        else:
            activity.logger.error(
                f"Failed to setup graph scenario: {result.get('error')}"
            )

        return SetupGraphScenarioOutput(
            scenario_id=result["scenario_id"],
            status=result["status"],
            graph_id=result.get("graph_id"),
            agent_definition_data=result.get("agent_definition_data"),
            no_of_rows=result.get("no_of_rows", 20),
            custom_columns=result.get("custom_columns"),
            property_list=result.get("property_list"),
            transcripts=result.get("transcripts"),
            custom_instruction=result.get("custom_instruction"),
            mode=result.get("mode", "voice"),
            error=result.get("error"),
            agent_context=result.get("agent_context"),
            configuration_snapshot=result.get("configuration_snapshot"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to setup graph scenario: {e}")
        return SetupGraphScenarioOutput(
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )


def _setup_graph_scenario_sync(
    scenario_id: str,
    validated_data: dict,
) -> dict:
    """Synchronous implementation of graph scenario setup."""
    import types

    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
        return None
    from model_hub.models.choices import StatusType
    from simulate.models import AgentDefinition, Scenarios
    from simulate.models.scenario_graph import ScenarioGraph
    from simulate.views.scenarios import convert_personas_to_property_list

    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        # Usage pre-check
        _sgs_org_id = None
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _sgs_org_id = str(scenario.organization.id)
            usage_check = check_usage(
                _sgs_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        agent_definition_id = validated_data.get("agent_definition_id")
        source_type = validated_data.get("source_type", "agent_definition")
        generate_graph = validated_data.get("generate_graph", False)
        graph_data = validated_data.get("graph")
        no_of_rows = validated_data.get("no_of_rows", 20)
        persona_ids = validated_data.get("personas", [])
        custom_columns = validated_data.get("custom_columns", [])
        transcripts = validated_data.get("transcripts", [])

        # --- Input validation ---
        if no_of_rows < 1:
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "Number of rows must be at least 1.",
            }

        if source_type != "prompt" and not agent_definition_id:
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "agent_definition_id is required.",
            }

        property_list = convert_personas_to_property_list(persona_ids)

        # Build agent_definition object
        if source_type == "prompt" and scenario.prompt_template:
            prompt_content = ""
            prompt_version = scenario.prompt_version
            if not prompt_version:
                prompt_version = scenario.prompt_template.all_executions.filter(
                    is_default=True, deleted=False
                ).first()

            if prompt_version and prompt_version.prompt_config_snapshot:
                config_snapshot = prompt_version.prompt_config_snapshot
                config = (
                    config_snapshot[0]
                    if isinstance(config_snapshot, list)
                    else config_snapshot
                )
                if isinstance(config, dict):
                    messages = config.get("messages", [])
                    formatted_messages = []
                    for msg in messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = [
                                p.get("text", "")
                                for p in content
                                if isinstance(p, dict) and p.get("type") == "text"
                            ]
                            content = "\n".join(text_parts)
                        if content:
                            formatted_messages.append(f"[{role}]: {content}")
                    prompt_content = "\n\n".join(formatted_messages)

            agent_description = (
                prompt_content or scenario.prompt_template.description or ""
            )

            agent_definition = types.SimpleNamespace(
                id=scenario.prompt_template.id,
                agent_name=scenario.prompt_template.name,
                description=agent_description,
                agent_type="text",
                languages=["en"],
                language="en",
                inbound=True,
                contact_number=None,
                organization=scenario.organization,
                workspace=scenario.workspace,
            )
        else:
            try:
                agent_definition = AgentDefinition.no_workspace_objects.get(
                    id=agent_definition_id
                )
            except AgentDefinition.DoesNotExist:
                return {
                    "scenario_id": scenario_id,
                    "status": "FAILED",
                    "error": f"Agent definition '{agent_definition_id}' not found. Please verify the agent_definition_id.",
                }

        # Update scenario source and mark as processing
        scenario.source = "Graph-based scenario"
        scenario.status = StatusType.PROCESSING.value
        scenario.save()

        scenario_graph = None
        graph_generator = None

        # Handle graph generation or use provided graph data
        from django.db import transaction

        if generate_graph:
            try:
                # Generate graph WITHOUT holding a DB transaction open.
                # LLM calls take minutes; holding transaction.atomic() that long
                # causes PgBouncer/Postgres to kill the idle connection,
                # resulting in "OperationalError: the connection is lost".
                graph_generator = ConversationGraphGenerator(
                    scenario=scenario,
                    agent_definition=agent_definition,
                )
                scenario_graph = graph_generator.generate_graph(save_to_db=True)
            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to generate graph: {str(e)}")

        elif graph_data:
            try:
                with transaction.atomic():
                    scenario_graph = ScenarioGraph.objects.create(
                        scenario=scenario,
                        name=f"{scenario.name} - Graph",
                        description=f"Graph for {scenario.name}",
                        organization=scenario.organization,
                        workspace=scenario.workspace,
                        graph_config={
                            "graph_data": graph_data,
                            "source": "user_provided",
                        },
                    )
            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to save graph data: {str(e)}")

        # Serialize agent definition for passing to other activities
        enhanced_agent = EnhancedScenariosAgent(
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
            agent_definition=agent_definition,
        )
        agent_definition_data = enhanced_agent.serialize_agent_definition()

        # Determine mode
        mode = enhanced_agent.mode

        # Extract custom_instruction from metadata
        custom_instruction = None
        if scenario and scenario.metadata:
            metadata = scenario.metadata
            if isinstance(metadata, str):
                try:
                    import json

                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            custom_instruction = metadata.get("custom_instruction")

        # Build flat agent_context for v3 activities (no ORM objects)
        agent_context = {
            "agent_name": getattr(agent_definition, "agent_name", ""),
            "description": getattr(agent_definition, "description", ""),
            "agent_type": str(getattr(agent_definition, "agent_type", "voice")),
            "languages": getattr(agent_definition, "languages", ["en"]),
            "language": getattr(agent_definition, "language", "en"),
            "inbound": getattr(agent_definition, "inbound", True),
            "contact_number": getattr(agent_definition, "contact_number", None),
            "agent_definition_id": str(getattr(agent_definition, "id", "")),
            "organization_id": (
                str(agent_definition.organization.id)
                if getattr(agent_definition, "organization", None)
                else None
            ),
            "workspace_id": (
                str(agent_definition.workspace.id)
                if getattr(agent_definition, "workspace", None)
                else None
            ),
            "mode": mode,
        }

        # Load configuration_snapshot from AgentVersion if available
        configuration_snapshot = None
        version_id = validated_data.get("agent_definition_version_id")
        if version_id:
            from simulate.models.agent_version import AgentVersion

            version = AgentVersion.objects.filter(id=version_id).first()
            if version:
                configuration_snapshot = version.configuration_snapshot

        # Usage emit (graph generation LLM cost)
        try:
            if _sgs_org_id and graph_generator:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _total_cost = graph_generator.sda.llm.cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(_total_cost)
                emit(
                    UsageEvent(
                        org_id=_sgs_org_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_graph_scenario_setup",
                            "source_id": str(scenario_id),
                            "raw_cost_usd": str(_total_cost),
                            **token_usage_properties(
                                graph_generator.sda.llm.token_usage
                            ),
                        },
                    )
                )
        except Exception:
            pass

        return {
            "scenario_id": scenario_id,
            "status": "COMPLETED",
            "graph_id": str(scenario_graph.id) if scenario_graph else None,
            "agent_definition_data": _ensure_json_serializable(agent_definition_data),
            "no_of_rows": no_of_rows,
            "custom_columns": _ensure_json_serializable(custom_columns),
            "property_list": _ensure_json_serializable(property_list),
            "transcripts": _ensure_json_serializable(transcripts),
            "custom_instruction": custom_instruction,
            "mode": mode,
            "agent_context": _ensure_json_serializable(agent_context),
            "configuration_snapshot": _ensure_json_serializable(configuration_snapshot),
        }

    except (ValueError, TypeError) as e:
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": str(e),
        }
    except Exception as e:
        logger.exception(f"System error in _setup_graph_scenario_sync: {e}")
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": "Scenario setup failed due to an internal error. Please try again.",
        }
    finally:
        close_old_connections()


@activity.defn
async def extract_intents_activity(
    input: ExtractIntentsInput,
) -> ExtractIntentsOutput:
    """
    Extract intents from transcripts or propose use cases from branches.

    Step 2: Generate intent_dict for case generation.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Extracting intents for scenario graph_id={input.graph_id}")

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _extract_intents_sync, thread_sensitive=False
            )(
                input.graph_id,
                input.agent_definition_data,
                input.transcripts,
                input.no_of_rows,
            )

        return ExtractIntentsOutput(
            status="COMPLETED",
            intent_dict=result.get("intent_dict", {}),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to extract intents: {e}")
        return ExtractIntentsOutput(
            status="FAILED",
            error=str(e),
        )


def _extract_intents_sync(
    graph_id: str,
    agent_definition_data: dict,
    transcripts: Any,
    no_of_rows: int = 20,
) -> dict:
    """Synchronous implementation of intent extraction."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
        from ee.agenthub.scenario_graph.prompt import USER_INTENT_PROMPT
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.graph_generator", exc_info=True)
        return None
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.utils.model_config import ModelConfigs

    try:
        close_old_connections()

        # Usage pre-check
        _ei_org_id = None
        try:
            from simulate.models.scenario_graph import ScenarioGraph
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _ei_graph = ScenarioGraph.objects.get(id=graph_id)
            _ei_org_id = str(_ei_graph.organization.id)
            usage_check = check_usage(
                _ei_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        agent_description = agent_definition_data.get("description", "")

        def get_user_intent(
            transcript: str, agent_desc: str, audio_url: str = None
        ) -> str:
            try:
                llm_config = ModelConfigs.VERTEX_GEMINI_2_5_PRO
                llm = LLM(
                    model_name=llm_config.model_name,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                    provider=llm_config.provider,
                )

                if audio_url:
                    # Pass audio directly to Gemini for intent extraction
                    prompt = USER_INTENT_PROMPT.format(
                        transcript="[Audio conversation provided below]",
                        agent_definition=agent_desc,
                    )
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": audio_url},
                                },
                            ],
                        }
                    ]
                else:
                    prompt = USER_INTENT_PROMPT.format(
                        transcript=transcript, agent_definition=agent_desc
                    )
                    messages = [{"role": "user", "content": prompt}]

                response = llm._get_completion_content(messages)
                return response.strip()
            except Exception:
                return "miscellaneous"

        intent_dict = {}

        # Handle transcripts - can be dict or list
        # Only process if it's a non-empty dict with .items() method
        if transcripts and isinstance(transcripts, dict) and len(transcripts) > 0:
            for key, transcript_dict in transcripts.items():
                audio_url = transcript_dict.get("audio_url")
                transcript_text = transcript_dict.get("transcript", "")
                intent = get_user_intent(
                    transcript_text, agent_description, audio_url=audio_url
                )
                intent_dict[key] = intent
        else:
            # Propose use cases from branches
            # Create a minimal agent definition for the graph generator
            import types

            minimal_agent_def = types.SimpleNamespace(
                id=agent_definition_data.get("id", ""),
                agent_name=agent_definition_data.get("agent_name", ""),
                description=agent_definition_data.get("description", ""),
                agent_type=agent_definition_data.get("agent_type", "voice"),
                languages=agent_definition_data.get("languages", ["en"]),
                language=agent_definition_data.get("language", "en"),
                inbound=agent_definition_data.get("inbound", True),
                contact_number=agent_definition_data.get("contact_number"),
                organization=None,
                workspace=None,
            )
            graph_generator = ConversationGraphGenerator(
                agent_definition=minimal_agent_def,
            )
            branches = graph_generator.get_branches(graph_id=graph_id)

            if branches:
                # Check Redis cache for intent proposals
                import hashlib

                from django.core.cache import cache as django_cache

                branches_json = json.dumps(
                    sorted(
                        branches,
                        key=lambda b: (b.get("start_node", ""), b.get("end_node", "")),
                    ),
                    ensure_ascii=False,
                )
                cache_hash = hashlib.md5(
                    (branches_json + agent_description).encode()
                ).hexdigest()
                cache_key = f"intent_cache:{graph_id}:{cache_hash}"

                try:
                    cached_intents = django_cache.get(cache_key)
                except Exception:
                    cached_intents = None

                if cached_intents is not None:
                    activity.logger.info(
                        f"Intent cache HIT for graph_id={graph_id}, "
                        f"cached_intent_count={len(cached_intents)}"
                    )
                    intents = cached_intents
                else:
                    # Use Flash model for intent proposal (classification task)
                    llm_config = ModelConfigs.VERTEX_GEMINI_2_5_FLASH
                    llm = LLM(
                        model_name=llm_config.model_name,
                        temperature=llm_config.temperature,
                        max_tokens=llm_config.max_tokens,
                        provider=llm_config.provider,
                    )
                    skip_patch = no_of_rows <= 20
                    intents = propose_use_cases_covering_workflows(
                        agent_definition=agent_description,
                        branches=branches,
                        llm=llm,
                        skip_coverage_patch=skip_patch,
                    )
                    # Usage emit
                    try:
                        if _ei_org_id:
                            try:
                                from ee.usage.schemas.event_types import BillingEventType
                            except ImportError:
                                BillingEventType = None
                            try:
                                from ee.usage.schemas.events import UsageEvent
                            except ImportError:
                                UsageEvent = None
                            try:
                                from ee.usage.services.config import BillingConfig
                            except ImportError:
                                BillingConfig = None
                            try:
                                from ee.usage.services.emitter import emit
                            except ImportError:
                                emit = None

                            _total_cost = llm.cost.get("total_cost", 0)
                            credits = BillingConfig.get().calculate_ai_credits(
                                _total_cost
                            )
                            emit(
                                UsageEvent(
                                    org_id=_ei_org_id,
                                    event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                                    amount=credits,
                                    properties={
                                        "source": "simulate_extract_intents",
                                        "source_id": str(graph_id),
                                        "raw_cost_usd": str(_total_cost),
                                        **token_usage_properties(llm.token_usage),
                                    },
                                )
                            )
                    except Exception:
                        pass
                    try:
                        django_cache.set(cache_key, intents, timeout=86400)
                    except Exception:
                        pass
                    activity.logger.info(
                        f"Intent cache MISS for graph_id={graph_id}, "
                        f"intent_count={len(intents)}, skip_coverage_patch={skip_patch}"
                    )

                try:
                    for idx, intent in enumerate(intents):
                        use_case_id = intent.get("use_case_id")
                        if use_case_id is None:
                            use_case_id = f"use_case_{idx}"
                        else:
                            use_case_id = str(use_case_id)
                        user_intent = str(intent.get("user_intent", "miscellaneous"))
                        intent_dict[use_case_id] = user_intent
                except Exception as e:
                    activity.logger.exception(f"Error proposing use cases: {e}")

        return {"intent_dict": _ensure_json_serializable(intent_dict)}

    finally:
        close_old_connections()


@activity.defn
async def process_branches_activity(
    input: ProcessBranchesInput,
) -> ProcessBranchesOutput:
    """
    Process branches from graph and return metadata.

    Step 3: Extract and process branches for case generation.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Processing branches for graph_id={input.graph_id}")

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _process_branches_sync, thread_sensitive=False
            )(
                input.graph_id,
                input.agent_definition_data,
                input.custom_instruction,
                input.no_of_rows,
                input.mode,
            )

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Processed {len(result.get('branches_metadata', []))} branches"
            )

        return ProcessBranchesOutput(
            status=result["status"],
            branches_metadata=result.get("branches_metadata"),
            branch_metadata_lookup=result.get("branch_metadata_lookup"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to process branches: {e}")
        return ProcessBranchesOutput(
            status="FAILED",
            error=str(e),
        )


def _process_branches_sync(
    graph_id: str,
    agent_definition_data: dict,
    custom_instruction: Optional[str],
    no_of_rows: int,
    mode: str,
) -> dict:
    """Synchronous implementation of branch processing."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
        return None

    try:
        close_old_connections()

        # Usage pre-check
        _pb_org_id = None
        try:
            from simulate.models.scenario_graph import ScenarioGraph
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _pb_graph = ScenarioGraph.objects.get(id=graph_id)
            _pb_org_id = str(_pb_graph.organization.id)
            usage_check = check_usage(
                _pb_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        # Reconstruct agent from serialized data
        agent = EnhancedScenariosAgent.from_serialized_agent_definition(
            agent_definition_data,
            no_of_rows=no_of_rows,
            simulation_mode=mode,
        )

        # Process branches
        branches_metadata, branch_metadata_lookup = agent.process_branches(
            graph_id=graph_id,
            custom_instruction=custom_instruction,
        )

        # Usage emit
        try:
            if _pb_org_id:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _total_cost = agent.llm.cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(_total_cost)
                emit(
                    UsageEvent(
                        org_id=_pb_org_id,
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_process_branches",
                            "source_id": str(graph_id),
                            "raw_cost_usd": str(_total_cost),
                            **token_usage_properties(agent.llm.token_usage),
                        },
                    )
                )
        except Exception:
            pass

        return {
            "status": "COMPLETED",
            "branches_metadata": _ensure_json_serializable(branches_metadata),
            "branch_metadata_lookup": _ensure_json_serializable(branch_metadata_lookup),
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def generate_cases_for_intent_activity(
    input: GenerateCasesForIntentInput,
) -> GenerateCasesForIntentOutput:
    """
    Generate cases for a single intent.

    Step 4: Called in parallel for each intent - the heaviest activity.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Generating cases for intent_id={input.intent_id}, "
        f"batch_size={input.batch_size}"
    )

    try:
        # Claim-check: load branches_metadata from Redis if key provided
        branches_metadata = input.branches_metadata
        if input.selected_metadata_redis_key:
            from tfc.utils.payload_storage import payload_storage

            loaded = payload_storage.retrieve_json(input.selected_metadata_redis_key)
            if loaded is not None:
                branches_metadata = loaded
            else:
                raise RuntimeError(
                    f"Redis key expired or missing for selected_metadata: "
                    f"{input.selected_metadata_redis_key}"
                )

        # Subset branches for diversity (round-robin across intents)
        if input.max_branches > 0 and len(branches_metadata) > input.max_branches:
            n = len(branches_metadata)
            subset = [
                branches_metadata[(input.branch_start_index + j) % n]
                for j in range(input.max_branches)
            ]
            branches_metadata = subset

        async with Heartbeater() as heartbeater:
            heartbeater.details = (
                "generating_cases",
                input.intent_id,
                input.batch_size,
            )

            result = await otel_sync_to_async(
                _generate_cases_for_intent_sync, thread_sensitive=False
            )(
                input.intent_id,
                input.intent_value,
                branches_metadata,
                input.agent_definition_data,
                input.batch_size,
                input.property_list,
                input.custom_columns,
                input.mode,
                input.graph_id,
                agent_context=input.agent_context,
                custom_instruction=input.custom_instruction,
                configuration_snapshot=input.configuration_snapshot,
            )

        # Claim-check: store cases in Redis for v3 path only.
        # v2 path (no agent_context) returns cases inline for backward compat.
        cases_redis_key = None
        cases_inline = result.get("cases")
        if result["status"] == "COMPLETED" and input.agent_context:
            # v3 path: store in Redis to avoid Temporal payload limits
            if cases_inline:
                from tfc.utils.payload_storage import payload_storage

                cases_redis_key = payload_storage.store_json(
                    cases_inline, ttl=SCENARIO_PAYLOAD_TTL
                )
                activity.logger.info(
                    f"Generated {len(cases_inline)} cases for "
                    f"intent_id={input.intent_id}, stored in Redis"
                )
            cases_inline = []  # Don't pass large data inline for v3

        return GenerateCasesForIntentOutput(
            status=result["status"],
            intent_id=input.intent_id,
            cases=cases_inline,  # Inline for v2, [] for v3
            cases_redis_key=cases_redis_key,
            categorized_branches=result.get("categorized_branches"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(
            f"Failed to generate cases for intent {input.intent_id}: {e}"
        )
        return GenerateCasesForIntentOutput(
            status="FAILED",
            intent_id=input.intent_id,
            error=str(e),
        )


def _generate_cases_for_intent_sync(
    intent_id: str,
    intent_value: str,
    branches_metadata: List[Dict[str, Any]],
    agent_definition_data: dict,
    batch_size: int,
    property_list: Optional[List[Dict]],
    custom_columns: Optional[List[Dict]],
    mode: str,
    graph_id: Optional[str],
    *,
    agent_context: Optional[Dict[str, Any]] = None,
    custom_instruction: Optional[str] = None,
    configuration_snapshot: Optional[Dict[str, Any]] = None,
) -> dict:
    """Synchronous implementation of case generation for a single intent.

    In v3 path, also categorizes branches after generation (ThreadPoolExecutor).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from django.db import close_old_connections

    try:
        close_old_connections()

        # Usage pre-check
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _gc_org_id = agent_context.get("organization_id") if agent_context else None
            if _gc_org_id:
                usage_check = check_usage(
                    str(_gc_org_id), BillingEventType.SYNTHETIC_DATA_GENERATION
                )
                if not usage_check.allowed:
                    raise ApplicationError(
                        usage_check.reason or "Usage limit exceeded",
                        non_retryable=True,
                    )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        if agent_context:
            # v3 path: use service (no ESA, no DB queries)
            try:
                from ee.agenthub.scenario_graph.services.case_generator import (
                    generate_cases_for_intent,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.services.case_generator", exc_info=True)
                return None

            cases = generate_cases_for_intent(
                intent_id=intent_id,
                intent_value=intent_value,
                branches_metadata=branches_metadata,
                batch_size=batch_size,
                agent_context=agent_context,
                mode=mode,
                custom_instruction=custom_instruction,
                configuration_snapshot=configuration_snapshot,
                custom_columns=custom_columns,
                property_list=property_list,
                graph_id=graph_id,
            )

            # v3: Categorize branches inline (ThreadPoolExecutor)
            categorized_branches = _categorize_branches_for_cases(cases)

            return {
                "status": "COMPLETED",
                "cases": _ensure_json_serializable(cases),
                "categorized_branches": _ensure_json_serializable(categorized_branches),
            }
        else:
            # v2 path: reconstruct from full serialized agent_definition_data
            try:
                from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
                    EnhancedScenariosAgent,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
                return None

            agent = EnhancedScenariosAgent.from_serialized_agent_definition(
                agent_definition_data,
                no_of_rows=batch_size,
                custom_columns=custom_columns,
                simulation_mode=mode,
            )

            cases = agent.generate_cases_for_single_intent(
                intent_id=intent_id,
                intent_value=intent_value,
                branches_metadata=branches_metadata,
                batch_size=batch_size,
                property_list=property_list,
                graph_id=graph_id,
            )

            try:
                if _gc_org_id:
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.config import BillingConfig
                    except ImportError:
                        BillingConfig = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    _cost = agent.llm.cost.get("total_cost", 0)
                    emit(
                        UsageEvent(
                            org_id=str(_gc_org_id),
                            event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                            amount=BillingConfig.get().calculate_ai_credits(_cost),
                            properties={
                                "source": "simulate_generate_cases_intent",
                                "source_id": str(intent_id),
                                "raw_cost_usd": str(_cost),
                                **token_usage_properties(agent.llm.token_usage),
                            },
                        )
                    )
            except Exception:
                pass

            return {
                "status": "COMPLETED",
                "cases": _ensure_json_serializable(cases),
            }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _generate_cases_for_intent_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to generate test cases. Please try again.",
        }
    finally:
        close_old_connections()


def _categorize_branches_for_cases(cases: List[Dict[str, Any]]) -> Dict[str, str]:
    """Categorize unique branches found in generated cases.

    Uses a single batched LLM call for all branches (falls back to per-branch
    if batch parsing fails).
    Returns dict mapping branch_name -> category.
    """
    try:
        from ee.agenthub.scenario_graph.services.category_service import (
            categorize_branches_batch,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.services.category_service", exc_info=True)
        return None

    # Collect unique branches with their situations
    branch_situations: Dict[str, List[str]] = {}
    for case in cases:
        branch_name = case.get("conversation_branch", "")
        if not branch_name:
            continue
        if branch_name not in branch_situations:
            branch_situations[branch_name] = []
        situation = case.get("situation", "")
        if situation:
            branch_situations[branch_name].append(situation)

    if not branch_situations:
        return {}

    # Limit situations per branch to avoid token overload (keep first 5)
    trimmed = {branch: sits[:5] for branch, sits in branch_situations.items()}

    return categorize_branches_batch(trimmed)


@activity.defn
async def categorize_and_validate_activity(
    input: CategorizeAndValidateInput,
) -> CategorizeAndValidateOutput:
    """
    Categorize branches and validate personas in cases.

    Step 5: Categorize, validate, and enrich cases.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Categorizing and validating {len(input.cases)} cases")

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _categorize_and_validate_sync, thread_sensitive=False
            )(
                input.cases,
                input.branch_metadata_lookup,
                input.mode,
                input.custom_columns,
            )

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Validated {len(result.get('validated_cases', []))} cases"
            )

        return CategorizeAndValidateOutput(
            status=result["status"],
            validated_cases=result.get("validated_cases"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to categorize and validate cases: {e}")
        return CategorizeAndValidateOutput(
            status="FAILED",
            error=str(e),
        )


def _categorize_and_validate_sync(
    cases: List[Dict[str, Any]],
    branch_metadata_lookup: Dict[str, Dict],
    mode: str,
    custom_columns: Optional[List[Dict]],
) -> dict:
    """Synchronous implementation of case categorization and validation."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
        return None

    try:
        close_old_connections()

        # Create a minimal agent just for validation methods
        import types

        dummy_agent_def = types.SimpleNamespace(
            id="",
            agent_name="",
            description="",
            agent_type="voice" if mode == "voice" else "text",
            languages=["en"],
            language="en",
            inbound=True,
            contact_number=None,
            organization=None,
            workspace=None,
        )

        agent = EnhancedScenariosAgent(
            no_of_rows=len(cases),
            custom_columns=custom_columns,
            simulation_mode=mode,
            agent_definition=dummy_agent_def,
        )

        # Categorize and validate
        validated_cases = agent.categorize_and_validate_cases(
            cases=cases,
            branch_metadata_lookup=branch_metadata_lookup,
        )

        return {
            "status": "COMPLETED",
            "validated_cases": _ensure_json_serializable(validated_cases),
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def create_scenario_dataset_activity(
    input: CreateScenarioDatasetInput,
) -> CreateScenarioDatasetOutput:
    """
    Create a Dataset from validated cases.

    Step 6: Persist cases to database as Dataset.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    try:
        # Claim-check: load cases from Redis if key provided
        cases = input.cases
        if input.cases_redis_key:
            from tfc.utils.payload_storage import payload_storage

            loaded = payload_storage.retrieve_json(input.cases_redis_key)
            if loaded is not None:
                cases = loaded
            else:
                raise RuntimeError(
                    f"Redis key expired or missing for validated_cases: "
                    f"{input.cases_redis_key}"
                )

        activity.logger.info(
            f"Creating dataset for scenario_id={input.scenario_id} "
            f"with {len(cases)} cases"
        )

        async with Heartbeater():
            result = await otel_sync_to_async(
                _create_scenario_dataset_sync, thread_sensitive=False
            )(
                input.scenario_id,
                cases,
                input.name,
                input.description,
                input.custom_columns,
                input.agent_definition_data,
                agent_context=input.agent_context,
            )

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Created dataset_id={result.get('dataset_id')} "
                f"for scenario_id={input.scenario_id}"
            )

        return CreateScenarioDatasetOutput(
            status=result["status"],
            dataset_id=result.get("dataset_id"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to create scenario dataset: {e}")
        return CreateScenarioDatasetOutput(
            status="FAILED",
            error=str(e),
        )


def _create_scenario_dataset_sync(
    scenario_id: str,
    cases: List[Dict[str, Any]],
    name: str,
    description: str,
    custom_columns: Optional[List[Dict]],
    agent_definition_data: Optional[Dict[str, Any]],
    *,
    agent_context: Optional[Dict[str, Any]] = None,
) -> dict:
    """Synchronous implementation of dataset creation."""
    from django.db import close_old_connections

    try:
        close_old_connections()

        if agent_context:
            # v3 path: use service (no ESA, no DB queries for agent reconstruction)
            try:
                from ee.agenthub.scenario_graph.services.dataset_persister import (
                    create_scenario_dataset,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.services.dataset_persister", exc_info=True)
                return None

            dataset = create_scenario_dataset(
                scenario_id=scenario_id,
                cases=cases,
                name=name,
                description=description,
                agent_context=agent_context,
                custom_columns=custom_columns,
            )
        elif agent_definition_data:
            # v2 path: reconstruct from full serialized agent_definition_data
            try:
                from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
                    EnhancedScenariosAgent,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
                return None

            agent = EnhancedScenariosAgent.from_serialized_agent_definition(
                agent_definition_data,
                no_of_rows=len(cases),
                custom_columns=custom_columns,
            )
            dataset = agent.create_scenario_dataset_from_cases(
                scenario_id=scenario_id,
                cases=cases,
                name=name,
                description=description,
            )
        else:
            # Fallback: create minimal dummy agent
            import types

            try:
                from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
                    EnhancedScenariosAgent,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.enhanced_scenarios_agent", exc_info=True)
                return None

            dummy_agent_def = types.SimpleNamespace(
                id="",
                agent_name="",
                description="",
                agent_type="voice",
                languages=["en"],
                language="en",
                inbound=True,
                contact_number=None,
                organization=None,
                workspace=None,
            )
            agent = EnhancedScenariosAgent(
                no_of_rows=len(cases),
                custom_columns=custom_columns,
                agent_definition=dummy_agent_def,
            )
            dataset = agent.create_scenario_dataset_from_cases(
                scenario_id=scenario_id,
                cases=cases,
                name=name,
                description=description,
            )

        return {
            "status": "COMPLETED",
            "dataset_id": str(dataset.id),
        }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _create_scenario_dataset_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to create the dataset. Please try again.",
        }
    finally:
        close_old_connections()


@activity.defn
async def finalize_graph_scenario_activity(
    input: FinalizeGraphScenarioInput,
) -> FinalizeGraphScenarioOutput:
    """
    Finalize the graph scenario creation.

    Step 7: Update scenario status, link dataset, store metadata.
    """
    from tfc.telemetry import otel_sync_to_async

    activity.logger.info(
        f"Finalizing graph scenario: scenario_id={input.scenario_id}, "
        f"dataset_id={input.dataset_id}"
    )

    try:
        result = await otel_sync_to_async(
            _finalize_graph_scenario_sync, thread_sensitive=False
        )(input.scenario_id, input.dataset_id, input.persona_ids)

        # Claim-check: clean up Redis keys (best-effort)
        if input.redis_keys_to_cleanup:
            try:
                from tfc.utils.payload_storage import payload_storage

                for key in input.redis_keys_to_cleanup:
                    try:
                        payload_storage.retrieve(key, delete_after=True)
                    except Exception:
                        pass  # TTL will handle cleanup
                activity.logger.info(
                    f"Cleaned up {len(input.redis_keys_to_cleanup)} Redis keys"
                )
            except Exception:
                pass  # Best-effort cleanup

        return FinalizeGraphScenarioOutput(
            scenario_id=result["scenario_id"],
            dataset_id=result.get("dataset_id"),
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to finalize graph scenario: {e}")
        return FinalizeGraphScenarioOutput(
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )


def _finalize_graph_scenario_sync(
    scenario_id: str,
    dataset_id: str,
    persona_ids: Optional[List[str]],
) -> dict:
    """Synchronous implementation of scenario finalization."""
    import json

    from django.db import close_old_connections

    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Dataset
    from simulate.models import Scenarios

    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        # If no valid dataset_id, mark scenario as FAILED
        if not dataset_id:
            scenario.status = StatusType.FAILED.value
            scenario.save()
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "No dataset_id provided",
            }

        dataset = Dataset.objects.get(id=dataset_id)

        scenario.status = StatusType.COMPLETED.value
        scenario.dataset = dataset

        # Store persona_ids in metadata if provided
        if persona_ids:
            current_metadata = scenario.metadata if scenario.metadata else {}
            if isinstance(current_metadata, str):
                try:
                    current_metadata = json.loads(current_metadata)
                except Exception:
                    current_metadata = {}
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
            scenario.metadata = current_metadata

        scenario.save()

        return {
            "scenario_id": scenario_id,
            "dataset_id": dataset_id,
            "status": "COMPLETED",
        }

    except Exception as e:
        # Try to mark scenario as failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


# =============================================================================
# Legacy Graph Scenario Activity (v1 - single activity, kept for compatibility)
# =============================================================================


@activity.defn
async def create_graph_scenario_activity(
    input: CreateGraphScenarioWorkflowInput,
) -> CreateGraphScenarioWorkflowOutput:
    """
    Create a graph-based scenario (legacy single-activity version).

    This is a direct port of create_graph_scenario_background_task.
    Uses provided/generated graph to create dataset using EnhancedScenariosAgent.
    Uses Heartbeater for automatic heartbeats during long-running graph generation.

    NOTE: This is the v1 activity kept for backward compatibility with existing workflows.
    New workflows should use the v2 multi-activity approach.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Creating graph scenario for scenario_id={input.scenario_id}")

    try:
        from simulate.models import Scenarios
        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        scenario = Scenarios.objects.select_related("dataset__organization").get(
            id=input.scenario_id
        )
        org_id = str(scenario.dataset.organization.id) if scenario.dataset else None
        if org_id:
            usage_check = check_usage(
                org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=usage_check.reason or "Usage limit exceeded",
                )

        async with Heartbeater():
            result = await otel_sync_to_async(
                _create_graph_scenario_sync, thread_sensitive=False
            )(input.scenario_id, input.validated_data)

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Graph scenario created successfully: scenario_id={input.scenario_id}, "
                f"dataset_id={result.get('dataset_id')}"
            )
        else:
            activity.logger.error(
                f"Failed to create graph scenario: {result.get('error')}"
            )

        return CreateGraphScenarioWorkflowOutput(
            scenario_id=result["scenario_id"],
            dataset_id=result.get("dataset_id"),
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to create graph scenario: {e}")
        return CreateGraphScenarioWorkflowOutput(
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Add Rows Activity
# =============================================================================


@activity.defn
async def generate_scenario_rows_activity(
    input: GenerateScenarioRowsInput,
) -> None:
    """
    Activity wrapper for generate_scenario_rows function.

    This wraps the complete generate_scenario_rows function which handles:
    - Setup generation (load dataset, extract branches)
    - Generate synthetic data (persona, situation, outcome)
    - Validate personas
    - Persist cells to database

    Uses Heartbeater for automatic heartbeats during long-running generation.
    """
    from simulate.tasks.scenario_tasks import generate_scenario_rows
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    try:
        async with Heartbeater() as heartbeater:
            heartbeater.details = ("generating_rows", input.num_rows)

            # Call the synchronous generate_scenario_rows function
            # Use thread_sensitive=False to allow thread pool execution for I/O-bound work
            await otel_sync_to_async(generate_scenario_rows, thread_sensitive=False)(
                input.dataset_id,
                input.scenario_id,
                input.num_rows,
                input.description,
                input.row_ids,
                input.sample_size_reference_data,
            )
    except Exception as e:
        activity.logger.exception(f"generate_scenario_rows_activity failed: {e}")
        # The generate_scenario_rows function already handles error cleanup
        # (updates cell/column statuses to FAILED internally)
        raise


# =============================================================================
# Graph Scenario Sub-Activities (v3 - granular, one unit of work each)
# =============================================================================


def _build_agent_adapter(agent_context: Dict[str, Any]) -> types.SimpleNamespace:
    """Build a lightweight SimpleNamespace adapter from a flat agent_context dict.

    This avoids DB queries for Organization/Workspace that
    from_serialized_agent_definition() would make.
    """
    return types.SimpleNamespace(
        id=agent_context.get("agent_definition_id", ""),
        agent_name=agent_context.get("agent_name", ""),
        description=agent_context.get("description", ""),
        agent_type=agent_context.get("agent_type", "voice"),
        languages=agent_context.get("languages", ["en"]),
        language=agent_context.get("language", "en"),
        inbound=agent_context.get("inbound", True),
        contact_number=agent_context.get("contact_number"),
        organization=None,
        workspace=None,
    )


@activity.defn
async def get_branches_activity(
    input: GetBranchesInput,
) -> GetBranchesOutput:
    """
    Get raw branch list from a conversation graph.

    v3 Step 3: Pure DB read — extract branches for fan-out processing.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Getting branches for graph_id={input.graph_id}")

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _get_branches_sync, thread_sensitive=False
            )(input.graph_id, input.agent_context)

        if result["status"] == "COMPLETED":
            activity.logger.info(f"Got {len(result.get('branches', []))} branches")

        return GetBranchesOutput(
            status=result["status"],
            branches=result.get("branches"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to get branches: {e}")
        return GetBranchesOutput(
            status="FAILED",
            error=str(e),
        )


def _get_branches_sync(
    graph_id: str,
    agent_context: Dict[str, Any],
) -> dict:
    """Synchronous implementation: get raw branches from graph."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.graph_generator", exc_info=True)
        return None

    try:
        close_old_connections()

        adapter = _build_agent_adapter(agent_context)
        graph_generator = ConversationGraphGenerator(
            agent_definition=adapter,
            simulation_mode=agent_context.get("mode", "voice"),
        )

        branches = graph_generator.get_branches(graph_id=graph_id)

        return {
            "status": "COMPLETED",
            "branches": _ensure_json_serializable(branches),
        }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _get_branches_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to retrieve branches from the graph. Please try again.",
        }
    finally:
        close_old_connections()


@activity.defn
async def process_single_branch_activity(
    input: ProcessSingleBranchInput,
) -> ProcessSingleBranchOutput:
    """
    Process a single branch: hydrate with messages, generate metadata via LLM.

    v3 Step 4: One per branch, fan-out at workflow level with semaphore.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    branch_path = input.branch.get("path", [])
    activity.logger.info(
        f"Processing single branch (path length={len(branch_path)}) "
        f"for graph_id={input.graph_id}"
    )

    try:
        async with Heartbeater() as heartbeater:
            heartbeater.details = ("processing_branch", len(branch_path))

            result = await otel_sync_to_async(
                _process_single_branch_sync, thread_sensitive=False
            )(input.branch, input.graph_id, input.agent_context, input.mode)

        if result["status"] == "COMPLETED":
            bm = result.get("branch_metadata", {})
            activity.logger.info(
                f"Processed branch: {bm.get('branch_name', 'unknown')}"
            )

        return ProcessSingleBranchOutput(
            status=result["status"],
            branch_metadata=result.get("branch_metadata"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to process single branch: {e}")
        return ProcessSingleBranchOutput(
            status="FAILED",
            error=str(e),
        )


def _process_single_branch_sync(
    branch: Dict[str, Any],
    graph_id: str,
    agent_context: Dict[str, Any],
    mode: str,
) -> dict:
    """Synchronous implementation: process one branch with LLM."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.graph_generator import (
            ConversationGraphGenerator,
        )
        from ee.agenthub.scenario_graph.services.branch_metadata import (
            create_branch_metadata_dict,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.graph_generator", exc_info=True)
        return None

    try:
        close_old_connections()

        # Usage pre-check
        _psb_org_id = agent_context.get("organization_id")
        try:
            if _psb_org_id:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.services.metering import check_usage
                except ImportError:
                    check_usage = None

                usage_check = check_usage(
                    str(_psb_org_id), BillingEventType.SYNTHETIC_DATA_GENERATION
                )
                if not usage_check.allowed:
                    raise ApplicationError(
                        usage_check.reason or "Usage limit exceeded",
                        non_retryable=True,
                    )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        adapter = _build_agent_adapter(agent_context)
        graph_generator = ConversationGraphGenerator(
            agent_definition_id=agent_context.get("agent_definition_id"),
            simulation_mode=mode,
            agent_definition=adapter,
        )

        # Step 1: Hydrate branch with messages and prompts (DB read)
        detailed_branch = graph_generator.get_branch_with_messages_and_prompts(
            branch, graph_id
        )

        branch_metadata, _branch_llm_cost = create_branch_metadata_dict(detailed_branch)

        # Usage emit
        try:
            if _psb_org_id:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _total_cost = _branch_llm_cost.get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(_total_cost)
                emit(
                    UsageEvent(
                        org_id=str(_psb_org_id),
                        event_type=BillingEventType.SYNTHETIC_DATA_GENERATION,
                        amount=credits,
                        properties={
                            "source": "simulate_process_single_branch",
                            "source_id": str(graph_id),
                            "raw_cost_usd": str(_total_cost),
                            **token_usage_properties(
                                _branch_llm_cost.get("token_usage")
                            ),
                        },
                    )
                )
        except Exception:
            pass

        return {
            "status": "COMPLETED",
            "branch_metadata": _ensure_json_serializable(branch_metadata),
        }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _process_single_branch_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to process branch. Please try again.",
        }
    finally:
        close_old_connections()


@activity.defn
async def select_branches_activity(
    input: SelectBranchesInput,
) -> SelectBranchesOutput:
    """
    Select/filter branches for case generation.

    v3 Step 5: LLM-based filtering if custom_instruction, else random sample.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Selecting branches: {len(input.branches_metadata)} available, "
        f"needed={input.needed}, has_custom_instruction={bool(input.custom_instruction)}"
    )

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _select_branches_sync, thread_sensitive=False
            )(input.branches_metadata, input.needed, input.custom_instruction)

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Selected {len(result.get('selected_metadata', []))} branches"
            )

        return SelectBranchesOutput(
            status=result["status"],
            selected_metadata=result.get("selected_metadata"),
            branch_metadata_lookup=result.get("branch_metadata_lookup"),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to select branches: {e}")
        return SelectBranchesOutput(
            status="FAILED",
            error=str(e),
        )


def _select_branches_sync(
    branches_metadata: List[Dict[str, Any]],
    needed: int,
    custom_instruction: Optional[str],
) -> dict:
    """Synchronous implementation: select/filter branches."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.services.branch_selector import (
            select_branches,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.services.branch_selector", exc_info=True)
        return None

    try:
        close_old_connections()

        if not branches_metadata:
            return {
                "status": "COMPLETED",
                "selected_metadata": [],
                "branch_metadata_lookup": {},
            }

        selected, branch_metadata_lookup = select_branches(
            branches_metadata, needed, custom_instruction
        )

        return {
            "status": "COMPLETED",
            "selected_metadata": _ensure_json_serializable(selected),
            "branch_metadata_lookup": _ensure_json_serializable(branch_metadata_lookup),
        }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _select_branches_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to select branches. Please try again.",
        }
    finally:
        close_old_connections()


@activity.defn
async def categorize_branch_activity(
    input: CategorizeBranchInput,
) -> CategorizeBranchOutput:
    """
    Categorize a single branch based on its situations.

    v3 Step 6b: One LLM call per branch. No agent_context needed.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Categorizing branch: {input.branch_name} ({len(input.situations)} situations)"
    )

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _categorize_branch_sync, thread_sensitive=False
            )(input.branch_name, input.situations)

        return CategorizeBranchOutput(
            status=result["status"],
            branch_name=result.get("branch_name", input.branch_name),
            category=result.get("category", ""),
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(
            f"Failed to categorize branch {input.branch_name}: {e}"
        )
        return CategorizeBranchOutput(
            status="FAILED",
            branch_name=input.branch_name,
            error=str(e),
        )


def _categorize_branch_sync(
    branch_name: str,
    situations: List[str],
) -> dict:
    """Synchronous implementation: categorize one branch via LLM."""
    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.services.category_service import (
            categorize_branch,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.services.category_service", exc_info=True)
        return None

    try:
        close_old_connections()

        category = categorize_branch(branch_name, situations)

        return {
            "status": "COMPLETED",
            "branch_name": branch_name,
            "category": category,
        }

    except Exception as e:
        logger.warning(f"Categorization failed for branch '{branch_name}': {e}")
        return {
            "status": "COMPLETED",
            "branch_name": branch_name,
            "category": "",
        }
    finally:
        close_old_connections()


@activity.defn
async def validate_and_enrich_cases_activity(
    input: ValidateAndEnrichCasesInput,
) -> ValidateAndEnrichCasesOutput:
    """
    Validate personas and enrich cases with branch data.

    v3 Step 7: Pure data transformation — no LLM calls, no DB access.
    """
    from tfc.telemetry import otel_sync_to_async

    try:
        # Claim-check: load large data from Redis if keys provided
        cases = input.cases or []
        categorized_branches = input.categorized_branches
        branch_metadata_lookup = input.branch_metadata_lookup

        if input.case_redis_keys or input.branch_metadata_lookup_redis_key:
            from tfc.utils.payload_storage import payload_storage

            if input.case_redis_keys:
                cases = []
                for key in input.case_redis_keys:
                    chunk = payload_storage.retrieve_json(key)
                    if chunk:
                        cases.extend(chunk)
                    else:
                        raise RuntimeError(
                            f"Redis key expired or missing for cases: {key}"
                        )

            if input.branch_metadata_lookup_redis_key:
                loaded = payload_storage.retrieve_json(
                    input.branch_metadata_lookup_redis_key
                )
                if loaded is not None:
                    branch_metadata_lookup = loaded
                else:
                    raise RuntimeError(
                        f"Redis key expired or missing for branch_metadata_lookup: "
                        f"{input.branch_metadata_lookup_redis_key}"
                    )

        activity.logger.info(
            f"Validating and enriching {len(cases)} cases "
            f"({len(categorized_branches)} branch categories)"
        )

        result = await otel_sync_to_async(
            _validate_and_enrich_cases_sync, thread_sensitive=False
        )(
            cases,
            categorized_branches,
            branch_metadata_lookup,
            input.mode,
            input.custom_columns,
        )

        # Claim-check: store validated cases in Redis
        validated_cases_redis_key = None
        if result["status"] == "COMPLETED":
            validated_cases = result.get("validated_cases")
            if validated_cases:
                from tfc.utils.payload_storage import payload_storage

                validated_cases_redis_key = payload_storage.store_json(
                    validated_cases, ttl=SCENARIO_PAYLOAD_TTL
                )
                activity.logger.info(
                    f"Validated {len(validated_cases)} cases, stored in Redis"
                )

        return ValidateAndEnrichCasesOutput(
            status=result["status"],
            validated_cases=[],  # Large data stored in Redis
            validated_cases_redis_key=validated_cases_redis_key,
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to validate and enrich cases: {e}")
        return ValidateAndEnrichCasesOutput(
            status="FAILED",
            error=str(e),
        )


def _validate_and_enrich_cases_sync(
    cases: List[Dict[str, Any]],
    categorized_branches: Dict[str, str],
    branch_metadata_lookup: Dict[str, Dict[str, Any]],
    mode: str,
    custom_columns: Optional[List[Dict[str, Any]]],
) -> dict:
    """Synchronous implementation: validate personas and enrich cases."""
    import math

    from django.db import close_old_connections

    try:
        from ee.agenthub.scenario_graph.services.persona_validator import (
            validate_persona,
        )
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.scenario_graph.services.persona_validator", exc_info=True)
        return None

    try:
        close_old_connections()

        if not cases:
            return {"status": "COMPLETED", "validated_cases": []}

        # 0. Deduplicate near-duplicate categories across intents
        try:
            from ee.agenthub.scenario_graph.services.category_service import (
                deduplicate_categories,
            )
        except ImportError:
            if settings.DEBUG:
                logger.warning("Could not import ee.agenthub.scenario_graph.services.category_service", exc_info=True)
            return None

        categorized_branches = deduplicate_categories(categorized_branches)

        # 1. Apply branch categories
        for case in cases:
            branch_name = case.get("conversation_branch", "")
            if branch_name in categorized_branches:
                case["branch_category"] = categorized_branches[branch_name]
            elif "branch_category" not in case:
                case["branch_category"] = ""

        # 2. Validate persona fields — uses service
        for case in cases:
            case["persona"] = validate_persona(case.get("persona", {}), mode)

        # 2b. Deduplicate persona names across intents
        # NOTE: Disabled — LLM-based name generation has high collision rates at
        # scale (500+ failures for 1000 rows) and causes activity timeouts.
        # Persona name diversity should be addressed upstream in SDA prompts.
        # from ee.agenthub.scenario_graph.services.persona_dedup import (
        #     deduplicate_persona_names,
        # )
        # deduplicate_persona_names(cases)

        # 3. Enrich with branch metadata
        for case in cases:
            branch_name = case.get("conversation_branch", "")
            branch_metadata = branch_metadata_lookup.get(branch_name)
            if branch_metadata:
                case["detailed_path"] = branch_metadata.get("detailedPath", [])
                case["start_node"] = branch_metadata.get("start_node", "unknown")
                case["end_node"] = branch_metadata.get("end_node", "unknown")
            else:
                case.setdefault("detailed_path", [])
                case.setdefault("start_node", "unknown")
                case.setdefault("end_node", "unknown")

        # 4. Sanitize None/NaN values
        for case in cases:
            for key, value in list(case.items()):
                if value is None:
                    case[key] = ""
                elif isinstance(value, float) and math.isnan(value):
                    case[key] = ""

        return {
            "status": "COMPLETED",
            "validated_cases": _ensure_json_serializable(cases),
        }

    except ValueError as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        logger.exception(f"System error in _validate_and_enrich_cases_sync: {e}")
        return {
            "status": "FAILED",
            "error": "Failed to validate cases. Please try again.",
        }
    finally:
        close_old_connections()


# =============================================================================
# v3 Chunky Pipeline: prepare_scenario_activity
# Merges old steps 1-5 into a single activity:
#   setup + extract_intents + get_branches + process_branch_metadata + select
# =============================================================================


@activity.defn
async def prepare_scenario_activity(
    input: PrepareScenarioInput,
) -> PrepareScenarioOutput:
    """Prepare everything needed for case generation in a single activity.

    Merges: setup_graph_scenario + extract_intents + get_branches +
    process_branch_metadata (ThreadPoolExecutor) + select_branches.

    This eliminates the per-branch activity fan-out (was 20-40 activities)
    and replaces it with in-process ThreadPoolExecutor parallelism.
    """
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Preparing scenario: scenario_id={input.scenario_id}")

    try:
        async with Heartbeater() as heartbeater:
            heartbeater.details = ("preparing_scenario", input.scenario_id)

            result = await otel_sync_to_async(
                _prepare_scenario_sync, thread_sensitive=False
            )(input.scenario_id, input.validated_data)

        if result["status"] == "COMPLETED":
            activity.logger.info(
                f"Scenario prepared: graph_id={result.get('graph_id')}, "
                f"intents={len(result.get('intent_dict', {}))}, "
                f"branches={len(result.get('selected_metadata', []))}"
            )

        # Claim-check: store large data in Redis, pass keys through Temporal
        selected_metadata_redis_key = None
        branch_metadata_lookup_redis_key = None
        if result["status"] == "COMPLETED":
            from tfc.utils.payload_storage import payload_storage

            selected_metadata = result.get("selected_metadata")
            branch_metadata_lookup = result.get("branch_metadata_lookup")
            if selected_metadata:
                selected_metadata_redis_key = payload_storage.store_json(
                    selected_metadata, ttl=SCENARIO_PAYLOAD_TTL
                )
            if branch_metadata_lookup:
                branch_metadata_lookup_redis_key = payload_storage.store_json(
                    branch_metadata_lookup, ttl=SCENARIO_PAYLOAD_TTL
                )

        return PrepareScenarioOutput(
            scenario_id=result.get("scenario_id", input.scenario_id),
            status=result["status"],
            graph_id=result.get("graph_id"),
            agent_context=result.get("agent_context"),
            agent_definition_data=result.get("agent_definition_data"),
            configuration_snapshot=result.get("configuration_snapshot"),
            no_of_rows=result.get("no_of_rows", 20),
            custom_columns=result.get("custom_columns"),
            property_list=result.get("property_list"),
            custom_instruction=result.get("custom_instruction"),
            mode=result.get("mode", "voice"),
            intent_dict=result.get("intent_dict"),
            selected_metadata=None,  # Large data stored in Redis
            branch_metadata_lookup=None,  # Large data stored in Redis
            error=result.get("error"),
            selected_metadata_redis_key=selected_metadata_redis_key,
            branch_metadata_lookup_redis_key=branch_metadata_lookup_redis_key,
            num_branches=len(result.get("selected_metadata") or []),
        )

    except Exception as e:
        activity.logger.exception(f"Failed to prepare scenario: {e}")
        return PrepareScenarioOutput(
            scenario_id=input.scenario_id,
            status="FAILED",
            error=str(e),
        )


def _prepare_scenario_sync(
    scenario_id: str,
    validated_data: dict,
) -> dict:
    """Synchronous implementation: full scenario preparation pipeline.

    Composes existing sync helpers sequentially:
    1. _setup_graph_scenario_sync → graph_id, agent_context, etc.
    2. _extract_intents_sync → intent_dict
    3. _get_branches_sync → raw branches
    4. _process_single_branch_sync × N (ThreadPoolExecutor) → branch metadata
    5. _select_branches_sync → selected_metadata, branch_metadata_lookup
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from django.db import close_old_connections

    try:
        close_old_connections()

        # Usage pre-check
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            _ps_scenario = Scenarios.objects.get(id=scenario_id)
            _ps_org_id = str(_ps_scenario.organization.id)
            usage_check = check_usage(
                _ps_org_id, BillingEventType.SYNTHETIC_DATA_GENERATION
            )
            if not usage_check.allowed:
                raise ApplicationError(
                    usage_check.reason or "Usage limit exceeded",
                    non_retryable=True,
                )
        except ApplicationError:
            raise
        except Exception:
            logger.warning("usage_precheck_failed", exc_info=True)

        # Step 1: Setup (load scenario, generate graph, build agent context)
        setup_result = _setup_graph_scenario_sync(scenario_id, validated_data)
        if setup_result["status"] != "COMPLETED":
            return setup_result

        graph_id = setup_result.get("graph_id")
        agent_context = setup_result.get("agent_context", {})
        agent_definition_data = setup_result.get("agent_definition_data", {})
        no_of_rows = setup_result.get("no_of_rows", 20)
        custom_instruction = setup_result.get("custom_instruction")
        mode = setup_result.get("mode", "voice")
        transcripts = setup_result.get("transcripts")

        if not graph_id:
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "No graph_id returned from setup",
            }

        # Steps 2 & 3: Extract intents + get branches in parallel
        # (both depend only on step 1 output, independent of each other)
        with ThreadPoolExecutor(max_workers=2) as pool:
            intents_future = pool.submit(
                _extract_intents_sync,
                graph_id,
                agent_definition_data,
                transcripts,
                no_of_rows,
            )
            branches_future = pool.submit(_get_branches_sync, graph_id, agent_context)
            intents_result = intents_future.result()
            branches_result = branches_future.result()

        intent_dict = intents_result.get("intent_dict", {})

        if branches_result["status"] != "COMPLETED":
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": branches_result.get("error", "Branch retrieval failed"),
            }

        branches = branches_result.get("branches", [])
        if not branches:
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "No branches found in graph",
            }

        # Step 4: Process branch metadata (ThreadPoolExecutor)
        all_branches_metadata = []
        num_workers = min(len(branches), 15)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = {
                pool.submit(
                    _process_single_branch_sync, branch, graph_id, agent_context, mode
                ): i
                for i, branch in enumerate(branches)
            }

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    if result["status"] == "COMPLETED" and result.get(
                        "branch_metadata"
                    ):
                        all_branches_metadata.append(result["branch_metadata"])
                except Exception as e:
                    logger.warning(f"Branch {idx} processing failed: {e}")

        if not all_branches_metadata:
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": "All branch processing failed",
            }

        logger.info(f"Processed {len(all_branches_metadata)}/{len(branches)} branches")

        # Step 5: Select branches
        select_result = _select_branches_sync(
            all_branches_metadata, no_of_rows, custom_instruction
        )
        if select_result["status"] != "COMPLETED":
            return {
                "scenario_id": scenario_id,
                "status": "FAILED",
                "error": select_result.get("error", "Branch selection failed"),
            }

        return {
            "scenario_id": scenario_id,
            "status": "COMPLETED",
            "graph_id": graph_id,
            "agent_context": setup_result.get("agent_context"),
            "agent_definition_data": setup_result.get("agent_definition_data"),
            "configuration_snapshot": setup_result.get("configuration_snapshot"),
            "no_of_rows": no_of_rows,
            "custom_columns": setup_result.get("custom_columns"),
            "property_list": setup_result.get("property_list"),
            "custom_instruction": custom_instruction,
            "mode": mode,
            "intent_dict": _ensure_json_serializable(intent_dict),
            "selected_metadata": select_result.get("selected_metadata"),
            "branch_metadata_lookup": select_result.get("branch_metadata_lookup"),
        }

    except Exception as e:
        logger.exception(f"System error in _prepare_scenario_sync: {e}")
        return {
            "scenario_id": scenario_id,
            "status": "FAILED",
            "error": f"Scenario preparation failed: {str(e)}",
        }
    finally:
        close_old_connections()


# =============================================================================
# Activity Registration
# =============================================================================

# All activities to register with Temporal workers
ALL_ACTIVITIES = [
    # Scenario Generation
    setup_generation_activity,
    generate_synthetic_data_activity,
    validate_personas_activity,
    persist_cells_activity,
    # Add Rows (uses complete generate_scenario_rows function)
    generate_scenario_rows_activity,
    # Add Columns (uses complete add_scenario_columns_task function)
    add_scenario_columns_activity,
    # Legacy Add Columns activities (kept for backwards compatibility)
    setup_columns_activity,
    generate_column_data_activity,
    persist_column_cells_activity,
    # Scenario Creation (legacy single-activity)
    create_dataset_scenario_activity,
    create_script_scenario_activity,
    create_graph_scenario_activity,
    # Graph Scenario Sub-Activities (v2 multi-activity)
    setup_graph_scenario_activity,
    extract_intents_activity,
    process_branches_activity,
    generate_cases_for_intent_activity,
    categorize_and_validate_activity,
    create_scenario_dataset_activity,
    finalize_graph_scenario_activity,
    # Graph Scenario Sub-Activities (v3 chunky pipeline)
    prepare_scenario_activity,
    validate_and_enrich_cases_activity,
    # Legacy v3 granular activities (kept for in-flight workflow compat)
    get_branches_activity,
    process_single_branch_activity,
    select_branches_activity,
    categorize_branch_activity,
]


__all__ = [
    # Scenario Generation Activities
    "setup_generation_activity",
    "generate_synthetic_data_activity",
    "validate_personas_activity",
    "persist_cells_activity",
    # Add Rows Activity
    "generate_scenario_rows_activity",
    # Add Columns Activity
    "add_scenario_columns_activity",
    # Legacy Add Columns Activities
    "setup_columns_activity",
    "generate_column_data_activity",
    "persist_column_cells_activity",
    # Scenario Creation Activities (legacy)
    "create_dataset_scenario_activity",
    "create_script_scenario_activity",
    "create_graph_scenario_activity",
    # Graph Scenario Sub-Activities (v2)
    "setup_graph_scenario_activity",
    "extract_intents_activity",
    "process_branches_activity",
    "generate_cases_for_intent_activity",
    "categorize_and_validate_activity",
    "create_scenario_dataset_activity",
    "finalize_graph_scenario_activity",
    # Graph Scenario Sub-Activities (v3 chunky pipeline)
    "prepare_scenario_activity",
    "validate_and_enrich_cases_activity",
    # Legacy v3 granular activities
    "get_branches_activity",
    "process_single_branch_activity",
    "select_branches_activity",
    "categorize_branch_activity",
    # Registration list
    "ALL_ACTIVITIES",
]
