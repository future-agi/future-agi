"""
Temporal activities for experiment runner.

These activities handle the individual units of work for running experiments.
Each activity is idempotent and can be retried safely.

Note: Django ORM is synchronous, so we use otel_sync_to_async to wrap database operations.
This ensures OTel context (trace/span info) is propagated to the sync thread.
"""

import uuid
from typing import Optional

from django.db import close_old_connections
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.common.heartbeat import Heartbeater

# Import types from separate module (avoids Django imports in workflows sandbox)
from tfc.temporal.experiments.types import (  # V2 types; Rerun cells types; Stop experiment types; Error eval cells types; Error agent cells types
    AnalyzeDependenciesInput,
    AnalyzeDependenciesOutput,
    CheckStatusInput,
    CheckStatusOutput,
    CleanupRunningCellsInput,
    CleanupRunningCellsOutput,
    CreateErrorAgentCellsInput,
    CreateErrorAgentCellsOutput,
    CreateErrorEvalCellsInput,
    CreateErrorEvalCellsOutput,
    GetEvalTemplatesInput,
    GetEvalTemplatesOutput,
    MarkExperimentRunningInput,
    MarkExperimentRunningOutput,
    PrepareAgentRowInput,
    PrepareAgentRowOutput,
    ProcessBatchEvalInput,
    ProcessBatchEvalOutput,
    ProcessPromptInput,
    ProcessPromptOutput,
    ProcessRowEvalInput,
    ProcessRowEvalOutput,
    ProcessRowInput,
    ProcessRowOutput,
    ResolveEdtColumnsInput,
    ResolveEdtColumnsOutput,
    RunEvaluationInput,
    RunEvaluationOutput,
    SetupAgentInput,
    SetupAgentOutput,
    SetupExperimentInput,
    SetupExperimentOutput,
    SetupPromptV2Input,
    SetupPromptV2Output,
    StopExperimentCleanupInput,
    StopExperimentCleanupOutput,
)

# =============================================================================
# Shared Helper Functions
# =============================================================================


def _get_config_ids(experiment) -> tuple[list, list]:
    """Return (prompt_config_ids, agent_config_ids) for an experiment.

    Queries ExperimentPromptConfig and ExperimentAgentConfig via their
    ExperimentDatasetTable join, ordered by ``order``.
    """
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentPromptConfig,
    )

    prompt_config_ids = list(
        ExperimentPromptConfig.objects.filter(
            experiment_dataset__experiment=experiment,
            experiment_dataset__deleted=False,
        )
        .order_by("order")
        .values_list("id", flat=True)
    )

    agent_config_ids = list(
        ExperimentAgentConfig.objects.filter(
            experiment_dataset__experiment=experiment,
            experiment_dataset__deleted=False,
        )
        .order_by("order")
        .values_list("id", flat=True)
    )

    return prompt_config_ids, agent_config_ids


def _resolve_row_ids(
    snapshot_dataset_id: str,
    rerun_row_ids: list | None,
    failed_only: bool,
    column_ids: list,
) -> list:
    """Return the list of row UUIDs to process.

    * If *rerun_row_ids* is given, those IDs are used directly.
    * Otherwise all non-deleted rows for the snapshot dataset are returned.
    * When *failed_only* is ``True`` **and** *rerun_row_ids* was **not**
      provided, the result is further filtered to rows that have at least
      one ``ERROR`` cell in the given *column_ids*.
    """
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Row

    if rerun_row_ids:
        row_ids = [
            uuid.UUID(rid) if not isinstance(rid, uuid.UUID) else rid
            for rid in rerun_row_ids
        ]
    else:
        rows = Row.objects.filter(
            dataset_id=snapshot_dataset_id, deleted=False
        ).order_by("order")
        row_ids = list(rows.values_list("id", flat=True))

    # Skip this filter when rerun_row_ids is provided because the API view
    # already reset those cells from ERROR -> RUNNING before starting the
    # workflow, so querying for ERROR status here would find nothing.
    if failed_only and row_ids and not rerun_row_ids:
        from model_hub.models.develop_dataset import Cell as CellModel

        error_row_ids = set(
            CellModel.objects.filter(
                column_id__in=column_ids,
                row_id__in=row_ids,
                dataset_id=snapshot_dataset_id,
                status=CellStatus.ERROR.value,
            ).values_list("row_id", flat=True)
        )
        row_ids = [rid for rid in row_ids if rid in error_row_ids]

    return row_ids


def _create_running_cells(row_ids: list, column_ids: list, dataset_id: str) -> None:
    """Bulk-create (or update) cells in RUNNING state for each column."""
    import json

    from model_hub.models.choices import CellStatus
    from model_hub.views.eval_runner import bulk_update_or_create_cells

    empty_values = {
        "value": "",
        "status": CellStatus.RUNNING.value,
        "value_infos": json.dumps({}),
    }
    for col_id in column_ids:
        bulk_update_or_create_cells(row_ids, col_id, dataset_id, empty_values)


# =============================================================================
# Synchronous Helper Functions (wrapped with otel_sync_to_async in activities)
# =============================================================================


def _setup_experiment_sync(experiment_id: str) -> dict:
    """Synchronous implementation of setup_experiment (V2: EPC/EAC based)."""
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.views.experiment_runner import ExperimentRunner

        runner = ExperimentRunner(uuid.UUID(experiment_id))
        runner.load_experiment()

        # Set status to RUNNING
        runner.experiment.status = StatusType.RUNNING.value
        runner.experiment.save(update_fields=["status"])

        # Prepare evaluation columns (critical for re-runs)
        runner.empty_or_create_evals_column()

        # V2: Get prompt/agent config IDs
        prompt_config_ids, agent_config_ids = _get_config_ids(runner.experiment)

        eval_template_ids = list(
            runner.experiment.user_eval_template_ids.values_list("id", flat=True)
        )

        activity.logger.info(
            f"Setup experiment {experiment_id}: "
            f"{len(prompt_config_ids)} prompts, {len(agent_config_ids)} agents, "
            f"{len(eval_template_ids)} evals, dataset {runner.dataset.id}"
        )

        return {
            "experiment_id": str(runner.experiment.id),
            "dataset_id": str(runner.dataset.id),
            "prompt_configs": [str(pc_id) for pc_id in prompt_config_ids],
            "eval_template_ids": [str(eid) for eid in eval_template_ids],
            "agent_configs": [str(ac_id) for ac_id in agent_config_ids],
            "experiment_type": runner.experiment.experiment_type,
            "has_base_column": runner.experiment.column_id is not None,
            "status": "READY",
        }
    finally:
        close_old_connections()


def _mark_experiment_failed_sync(experiment_id: str) -> None:
    """Synchronous implementation of mark_experiment_failed."""
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.models.experiments import ExperimentsTable

        ExperimentsTable.objects.filter(id=experiment_id).update(
            status=StatusType.FAILED.value
        )
    finally:
        close_old_connections()


def _mark_experiment_running_sync(experiment_id: str) -> dict:
    """Mark experiment as RUNNING without resetting eval columns.

    Used by RerunCellsV2Workflow which does NOT need to reset eval data
    (only specific cells are being rerun).  Also used by the column-change-only
    and selective re-run paths in RunExperimentV2Workflow.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.models.experiments import ExperimentsTable

        experiment = ExperimentsTable.objects.get(id=uuid.UUID(experiment_id))
        experiment.status = StatusType.RUNNING.value
        experiment.save(update_fields=["status"])

        eval_template_ids = list(
            experiment.user_eval_template_ids.values_list("id", flat=True)
        )

        prompt_config_ids, agent_config_ids = _get_config_ids(experiment)

        return {
            "experiment_id": str(experiment.id),
            "dataset_id": str(experiment.snapshot_dataset_id),
            "prompt_configs": [str(pc_id) for pc_id in prompt_config_ids],
            "agent_configs": [str(ac_id) for ac_id in agent_config_ids],
            "eval_template_ids": [str(eid) for eid in eval_template_ids],
            "has_base_column": experiment.column_id is not None,
            "status": "READY",
        }
    except Exception as e:
        activity.logger.exception(
            f"Error marking experiment {experiment_id} running: {e}"
        )
        raise
    finally:
        close_old_connections()


def _cleanup_running_cells_sync(experiment_id: str) -> dict:
    """
    Mark all running cells as error for a SPECIFIC experiment only.

    This is used to cleanup cells that were left in 'running' state
    when a workflow was terminated or failed unexpectedly.

    IMPORTANT: Only cleans up cells belonging to THIS experiment's columns,
    not all experiment columns in the dataset. This allows multiple experiments
    on the same dataset to run concurrently without interfering with each other.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import CellStatus
        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.experiments import ExperimentsTable

        experiment = ExperimentsTable.objects.get(
            id=uuid.UUID(experiment_id), deleted=False
        )

        # Get columns for THIS experiment only via FK relationship:
        # experiment -> experiment_datasets -> columns
        all_column_ids = list(
            Column.objects.filter(
                experiments_dataset_column__experiment=experiment,
                deleted=False,
            ).values_list("id", flat=True)
        )

        if not all_column_ids:
            return {
                "experiment_id": experiment_id,
                "cells_cleaned": 0,
                "status": "COMPLETED",
            }

        # Update running cells to error ONLY for this experiment's columns
        cells_updated = Cell.objects.filter(
            column_id__in=all_column_ids,
            status=CellStatus.RUNNING.value,
            deleted=False,
        ).update(status=CellStatus.ERROR.value)

        activity.logger.info(
            f"Cleaned up {cells_updated} running cells for experiment {experiment_id} "
            f"(columns: {len(all_column_ids)})"
        )

        return {
            "experiment_id": experiment_id,
            "cells_cleaned": cells_updated,
            "status": "COMPLETED",
        }

    except Exception as e:
        activity.logger.exception(f"Error in _cleanup_running_cells_sync: {e}")
        return {
            "experiment_id": experiment_id,
            "cells_cleaned": 0,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _process_prompt_sync(
    experiment_id: str,
    dataset_id: str,
    prompt_config: dict,
    model_spec,
) -> dict:
    """
    Synchronous implementation of process_prompt.

    Creates ExperimentDatasetTable, Column, and Cells.
    Returns all info needed for row processing.
    """
    close_old_connections()

    try:
        import json

        from model_hub.models.choices import CellStatus, SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )
        from model_hub.services.column_service import create_experiment_column
        from model_hub.views.eval_runner import bulk_update_or_create_cells
        from model_hub.views.experiment_runner import normalize_voice_config

        experiment = ExperimentsTable.objects.select_related(
            "dataset", "dataset__organization"
        ).get(id=experiment_id, deleted=False)
        dataset = experiment.dataset

        # Handle both string and dict model specifications
        if isinstance(model_spec, dict):
            model = model_spec.get("name")
            if not model:
                raise ValueError(
                    f"ModelSpec missing required 'name' field: {model_spec}"
                )
            run_prompt_config_for_model = model_spec.get("config", {})
            display_suffix = model_spec.get("display_name") or model
        else:
            model = model_spec
            display_suffix = model
            run_prompt_config_for_model = prompt_config.get(
                "run_prompt_config", {}
            ).get(model)

        # Merge model-specific parameters from modelParams
        if run_prompt_config_for_model is None:
            run_prompt_config_for_model = {}
        model_params_from_payload = prompt_config.get("model_params", {}).get(model, {})
        run_prompt_config_for_model.update(model_params_from_payload)

        # Normalize voice configuration
        if dataset and dataset.organization:
            run_prompt_config_for_model = normalize_voice_config(
                run_prompt_config_for_model, dataset.organization.id
            )

        # Include voice in column suffix for audio output
        try:
            voice = None
            if isinstance(run_prompt_config_for_model, dict):
                voice = run_prompt_config_for_model.get("voice")
            if prompt_config.get("output_format") == "audio" and voice is not None:
                voice_str = str(voice).strip()
                if voice_str and voice_str.lower() not in str(display_suffix).lower():
                    display_suffix = f"{display_suffix}-{voice_str}"
        except Exception:
            pass

        # Column name matches original
        column_name = f"{experiment.name}-{prompt_config['name']}-{display_suffix}"

        # Check if entry exists within THIS experiment (for reruns)
        experiment_dataset = ExperimentDatasetTable.objects.filter(
            experiment=experiment, name=column_name
        ).first()

        dataset_created = False

        if not experiment_dataset:
            try:
                experiment_dataset, dataset_created = (
                    ExperimentDatasetTable.objects.get_or_create(
                        name=column_name,
                        experiment=experiment,
                        defaults={
                            "legacy_prompt_config": prompt_config,
                            "status": StatusType.RUNNING.value,
                        },
                    )
                )
            except ExperimentDatasetTable.MultipleObjectsReturned:
                experiment_dataset = ExperimentDatasetTable.objects.filter(
                    experiment=experiment, name=column_name
                ).first()
                dataset_created = False

        # Update status to RUNNING for re-runs
        if experiment_dataset and not dataset_created:
            experiment_dataset.status = StatusType.RUNNING.value
            experiment_dataset.save(update_fields=["status"])

        output_format = prompt_config.get("output_format", "string")
        # Create column for experiment results
        column, column_created = create_experiment_column(
            dataset=dataset,
            source_id=experiment_dataset.id,
            name=column_name,
            output_format=output_format,
            response_format=prompt_config.get("response_format"),
            status=StatusType.RUNNING.value,
        )

        if column_created:
            experiment_dataset.columns.add(column)

        # FK is set during get_or_create, no M2M add needed

        # Get all rows
        rows = Row.objects.filter(dataset_id=dataset.id, deleted=False).order_by(
            "order"
        )
        row_ids = list(rows.values_list("id", flat=True))

        # Bulk create cells in RUNNING state
        empty_values = {
            "value": "",
            "status": CellStatus.RUNNING.value,
            "value_infos": json.dumps({}),
        }
        bulk_update_or_create_cells(row_ids, column.id, dataset.id, empty_values)

        activity.logger.info(
            f"Processed prompt for experiment {experiment_id}: "
            f"column {column.id}, {len(row_ids)} rows, model {model}"
        )

        return {
            "experiment_id": experiment_id,
            "column_id": str(column.id),
            "experiment_dataset_id": str(experiment_dataset.id),
            "row_ids": [str(rid) for rid in row_ids],
            "messages": prompt_config.get("messages", []),
            "model_name": model,
            "model_config": prompt_config.get("configuration", {}),
            "output_format": output_format,
            "run_prompt_config": run_prompt_config_for_model,
            "status": "READY",
        }
    except Exception as e:
        activity.logger.exception(f"Error in _process_prompt_sync: {e}")
        raise
    finally:
        close_old_connections()


def _setup_prompt_v2_sync(
    experiment_id: str,
    dataset_id: str,
    experiment_prompt_config_id: str,
    rerun_row_ids: list = None,
    failed_only: bool = False,
) -> dict:
    """
    V2: Setup a single prompt for processing.

    EDT + EPC already exist (created by V2 view).
    Creates result Column + Cells in RUNNING state.
    Returns all info needed for row processing.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.models.experiments import ExperimentPromptConfig
        from model_hub.services.experiment_config_resolver import resolve_prompt_config
        from model_hub.views.experiment_runner import normalize_voice_config

        # Load the EPC with related EDT and experiment
        epc = ExperimentPromptConfig.objects.select_related(
            "experiment_dataset",
            "experiment_dataset__experiment",
            "experiment_dataset__experiment__dataset",
            "experiment_dataset__experiment__dataset__organization",
            "prompt_version",
        ).get(id=experiment_prompt_config_id)

        experiment = epc.experiment_dataset.experiment
        experiment_dataset = epc.experiment_dataset
        snapshot_dataset_id = str(experiment.snapshot_dataset_id)

        # Resolve config from EPC
        config = resolve_prompt_config(epc)

        model_name = config["model"]
        # get_messages() returns prompt_version.prompt_config_snapshot for LLM
        # experiments, which is the full snapshot dict with nested "messages",
        # "configuration", and "placeholders" keys. Extract the inner messages list.
        raw_messages = config["messages"]
        if isinstance(raw_messages, dict) and "messages" in raw_messages:
            messages = raw_messages["messages"]
        else:
            messages = raw_messages
        output_format = config.get("output_format", "string")

        # Build model-specific run config (merge model_config + model_params)
        run_prompt_config = dict(config.get("model_config", {}) or {})
        run_prompt_config.update(config.get("model_params", {}) or {})

        # Normalize voice configuration
        if experiment.dataset and experiment.dataset.organization_id:
            run_prompt_config = normalize_voice_config(
                run_prompt_config, experiment.dataset.organization_id
            )

        # Set EDT status to RUNNING
        experiment_dataset.status = StatusType.RUNNING.value
        experiment_dataset.save(update_fields=["status"])

        # Column name from EPC name
        column_name = epc.name
        # Use create_experiment_column (same as V1) to correctly determine
        # data_type from output_format + response_format (e.g., json → "json").
        from model_hub.services.column_service import create_experiment_column

        column, column_created = create_experiment_column(
            dataset=experiment.snapshot_dataset,
            source_id=experiment_dataset.id,
            name=column_name,
            output_format=output_format,
            response_format=run_prompt_config.get("response_format"),
            status=StatusType.RUNNING.value,
        )

        if column_created:
            experiment_dataset.columns.add(column)
        else:
            column.status = StatusType.RUNNING.value
            column.save(update_fields=["status"])

        # Resolve rows and create RUNNING cells
        row_ids = _resolve_row_ids(
            snapshot_dataset_id, rerun_row_ids, failed_only, [column.id]
        )
        _create_running_cells(row_ids, [column.id], snapshot_dataset_id)

        activity.logger.info(
            f"Setup prompt V2 for experiment {experiment_id}: "
            f"epc {experiment_prompt_config_id}, column {column.id}, "
            f"{len(row_ids)} rows, model {model_name}"
        )

        return {
            "experiment_id": str(experiment.id),
            "experiment_prompt_config_id": experiment_prompt_config_id,
            "column_id": str(column.id),
            "experiment_dataset_id": str(experiment_dataset.id),
            "row_ids": [str(rid) for rid in row_ids],
            "messages": messages,
            "model_name": model_name,
            "model_config": config.get("configuration", {}),
            "output_format": output_format,
            "run_prompt_config": run_prompt_config,
            "status": "READY",
        }
    except Exception as e:
        activity.logger.exception(f"Error in _setup_prompt_v2_sync: {e}")
        raise
    finally:
        close_old_connections()


def _setup_agent_sync(
    experiment_id: str,
    dataset_id: str,
    experiment_agent_config_id: str,
    rerun_row_ids: list = None,
    failed_only: bool = False,
) -> dict:
    """
    V2: Setup an agent experiment entry for processing.

    EDT + EAC already exist (created by V2 view).
    Creates one result Column per LLM node + Cells in RUNNING state.
    Returns node_column_mapping + row_ids for row processing.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.models.develop_dataset import Column
        from model_hub.models.experiments import ExperimentAgentConfig

        # Load the EAC with related EDT and experiment
        eac = ExperimentAgentConfig.objects.select_related(
            "experiment_dataset",
            "experiment_dataset__experiment",
            "graph_version",
            "graph_version__graph",
        ).get(id=experiment_agent_config_id)

        experiment = eac.experiment_dataset.experiment
        experiment_dataset = eac.experiment_dataset
        snapshot_dataset_id = str(experiment.snapshot_dataset_id)

        # Set EDT status to RUNNING
        experiment_dataset.status = StatusType.RUNNING.value
        experiment_dataset.save(update_fields=["status"])

        # Create output columns (idempotent — reuses existing columns)
        from model_hub.views.experiment_runner import ExperimentRunner

        node_column_mapping, terminal_column_ids = (
            ExperimentRunner.create_agent_output_columns(eac, snapshot_dataset_id)
        )

        # Workflow needs columns in RUNNING state for processing
        Column.objects.filter(id__in=list(node_column_mapping.values())).update(
            status=StatusType.RUNNING.value
        )

        # Resolve rows and create RUNNING cells for all agent columns
        agent_column_ids = list(node_column_mapping.values())
        row_ids = _resolve_row_ids(
            snapshot_dataset_id, rerun_row_ids, failed_only, agent_column_ids
        )
        _create_running_cells(row_ids, agent_column_ids, snapshot_dataset_id)

        activity.logger.info(
            f"Setup agent for experiment {experiment_id}: "
            f"eac {experiment_agent_config_id}, {len(node_column_mapping)} columns, "
            f"{len(row_ids)} rows"
        )

        return {
            "experiment_id": str(experiment.id),
            "experiment_agent_config_id": experiment_agent_config_id,
            "experiment_dataset_id": str(experiment_dataset.id),
            "graph_version_id": str(eac.graph_version_id),
            "node_column_mapping": node_column_mapping,
            "terminal_column_ids": terminal_column_ids,
            "row_ids": [str(rid) for rid in row_ids],
            "status": "READY",
        }
    except Exception as e:
        activity.logger.exception(f"Error in _setup_agent_sync: {e}")
        raise
    finally:
        close_old_connections()


def _prepare_agent_row_sync(
    row_id: str,
    dataset_id: str,
    experiment_id: str,
    graph_version_id: str,
) -> dict:
    """
    Prepare a single row for graph execution.

    Reads base-data cells from the snapshot dataset, builds an input_payload
    mapping column names to values, and creates a GraphExecution record via
    the agent_playground client helper.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Cell
        from tfc.temporal.agent_playground.client import _create_graph_execution

        # Experiment-generated column sources to exclude from input
        EXCLUDE_SOURCES = {
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        }

        # Get base-data cells for this row (exclude experiment-generated columns)
        cells = (
            Cell.objects.filter(
                row_id=row_id,
                column__dataset_id=dataset_id,
                deleted=False,
            )
            .exclude(column__source__in=EXCLUDE_SOURCES)
            .select_related("column")
        )

        input_payload = {cell.column.name: cell.value for cell in cells}

        # Create GraphExecution via graph engine helper
        graph_execution_id = _create_graph_execution(
            graph_version_id=uuid.UUID(graph_version_id),
            input_payload=input_payload,
        )

        activity.logger.info(
            f"Prepared agent row {row_id}: "
            f"graph_execution {graph_execution_id}, {len(input_payload)} input keys"
        )

        return {
            "row_id": row_id,
            "graph_execution_id": graph_execution_id,
            "input_payload": input_payload,
            "status": "READY",
        }
    except Exception as e:
        activity.logger.exception(f"Error in _prepare_agent_row_sync: {e}")
        return {
            "row_id": row_id,
            "graph_execution_id": "",
            "input_payload": {},
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _process_row_sync(
    row_id: str,
    column_id: str,
    dataset_id: str,
    experiment_id: str,
    messages: list,
    model: str,
    model_config: dict,
    output_format: Optional[str],
    run_prompt_config: Optional[dict],
) -> dict:
    """Synchronous implementation of process_row."""
    close_old_connections()

    try:
        from model_hub.views.experiment_runner import _process_row_impl

        # Call existing implementation (skip Celery evals - Temporal handles them separately)
        _process_row_impl(
            uuid.UUID(row_id),
            uuid.UUID(column_id),
            uuid.UUID(dataset_id),
            uuid.UUID(experiment_id),
            messages,
            model,
            model_config,
            output_format,
            run_prompt_config,
            skip_celery_evals=True,
        )

        # NOTE: Do NOT call check_and_update_column_status here!
        # In Temporal, evaluations run AFTER all rows complete (in the workflow).
        # If we check column status here, the result column will be marked COMPLETED
        # before evaluations finish, causing the experiment to complete prematurely.
        # The workflow handles status checks at the right time (after evals complete).

        return {
            "row_id": row_id,
            "column_id": column_id,
            "status": "COMPLETED",
        }
    except Exception as e:
        from model_hub.models.choices import CellStatus
        from model_hub.models.develop_dataset import Cell

        activity.logger.exception(f"Error processing row {row_id}: {e}")

        # Mark cell as ERROR
        try:
            Cell.objects.filter(
                row_id=row_id, column_id=column_id, deleted=False
            ).update(status=CellStatus.ERROR.value)
        except Exception:
            pass

        # NOTE: Do NOT call check_and_update_column_status here!
        # The workflow handles status checks after all rows and evals complete.
        # Checking here would cause premature experiment completion.

        raise
    finally:
        close_old_connections()


def _create_skipped_eval_cells(
    dataset,
    user_eval_metric,
    error_message: str,
) -> None:
    """Create error cells for an eval that cannot run in base column context."""
    import json

    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Column, Row
    from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type
    from model_hub.views.eval_runner import bulk_update_or_create_cells

    data_type = infer_eval_result_column_data_type(user_eval_metric.template)

    eval_column, _ = Column.objects.get_or_create(
        name=user_eval_metric.name,
        source=SourceChoices.EVALUATION.value,
        source_id=str(user_eval_metric.id),
        dataset=dataset,
        defaults={"data_type": data_type},
    )

    row_ids = list(
        Row.objects.filter(dataset=dataset, deleted=False).values_list("id", flat=True)
    )

    error_value = json.dumps({"error": error_message})
    cell_values = {
        "value": error_value,
        "status": CellStatus.ERROR.value,
        "value_infos": json.dumps({"reason": error_message}),
    }

    bulk_update_or_create_cells(
        row_ids,
        eval_column.id,
        dataset.id,
        cell_values,
        user_eval_metric_id=user_eval_metric.id,
    )

    if user_eval_metric.config.get("reason_column"):
        reason_column, _ = Column.objects.get_or_create(
            name=f"{user_eval_metric.name}-reason",
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_column.id}-sourceid-{user_eval_metric.id}",
            dataset=dataset,
            defaults={"data_type": "text"},
        )
        reason_values = {
            "value": error_message,
            "status": CellStatus.ERROR.value,
            "value_infos": json.dumps({"reason": error_message}),
        }
        bulk_update_or_create_cells(
            row_ids,
            reason_column.id,
            dataset.id,
            reason_values,
            user_eval_metric_id=user_eval_metric.id,
        )


def _run_base_evaluation_sync(
    experiment_id: str,
    eval_template_id: str,
    row_ids: list = None,
    heartbeater: Optional["Heartbeater"] = None,
    batch_size: int = 10,
) -> dict:
    """
    Synchronous implementation of run_base_evaluation.

    Mirrors the logic of run_base_col_eval_task Celery task.
    Processes rows in batches with heartbeats to avoid timeout.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import CellStatus, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable
        from model_hub.services.experiment_utils import (
            is_experiment_cancelled,
            maybe_complete_experiment_after_eval_stop,
        )
        from model_hub.utils.eval_cell_status import mark_eval_cells_stopped
        from model_hub.views.eval_runner import EvaluationRunner
        from tfc.utils.distributed_state import evaluation_tracker

        # Guard: skip if experiment was already cancelled
        if is_experiment_cancelled(uuid.UUID(experiment_id)):
            return {
                "experiment_id": experiment_id,
                "eval_template_id": eval_template_id,
                "status": "SKIPPED",
            }

        # Guard: skip if user already pressed Stop on this specific eval
        # before the activity got a chance to run.
        if evaluation_tracker.should_cancel(uuid.UUID(eval_template_id)):
            evaluation_tracker.clear_cancel_flag(uuid.UUID(eval_template_id))
            maybe_complete_experiment_after_eval_stop(experiment_id)
            return {
                "experiment_id": experiment_id,
                "eval_template_id": eval_template_id,
                "status": "CANCELLED",
            }

        experiment = ExperimentsTable.objects.select_related(
            "snapshot_dataset", "column"
        ).get(id=uuid.UUID(experiment_id), deleted=False)

        if not experiment.column:
            activity.logger.info(
                f"Skipping base evaluation {eval_template_id} — no base column"
            )
            return {
                "experiment_id": experiment_id,
                "eval_template_id": eval_template_id,
                "status": "SKIPPED",
            }

        dataset = experiment.snapshot_dataset
        user_eval_metric = UserEvalMetric.objects.get(id=uuid.UUID(eval_template_id))

        # Check if column exists and has cells — skip re-running if already done
        column_qs = Column.objects.filter(
            source_id=str(user_eval_metric.id), dataset=dataset, deleted=False
        )
        column_exists = column_qs.exists()
        has_cells = (
            column_qs.first().cell_set.filter(deleted=False).exists()
            if column_exists
            else False
        )

        activity.logger.info(
            f"Base evaluation {eval_template_id}: column_exists={column_exists}, has_cells={has_cells}"
        )

        # Skip if column already has cells (initial run already done).
        # When row_ids is provided, this is a cell-level rerun — don't skip.
        if column_exists and has_cells and not row_ids:
            return {
                "experiment_id": experiment_id,
                "eval_template_id": eval_template_id,
                "status": "SKIPPED",
            }

        # Evals that reference prompt_chain cannot run in base eval context
        # unless the base column is a run_prompt column (we can resolve
        # prompt_chain from the RunPrompter's messages in that case).
        mapping = (user_eval_metric.config or {}).get("mapping", {})
        has_prompt_chain = any(
            isinstance(v, str) and v.lower() == "prompt_chain" for v in mapping.values()
        )
        is_run_prompt_base = (
            experiment.column
            and experiment.column.source == SourceChoices.RUN_PROMPT.value
        )
        if has_prompt_chain and not is_run_prompt_base:
            activity.logger.info(
                f"Base evaluation {eval_template_id} uses prompt_chain — "
                f"creating error cells (prompt_chain requires per-prompt context "
                f"or a run_prompt base column)"
            )
            _create_skipped_eval_cells(
                dataset=dataset,
                user_eval_metric=user_eval_metric,
                error_message=(
                    "Evaluation skipped: this eval uses prompt_chain which is "
                    "only available in per-prompt/agent context, "
                    "not on the base column"
                ),
            )
            return {
                "experiment_id": experiment_id,
                "eval_template_id": eval_template_id,
                "status": "COMPLETED",
            }

        runner = EvaluationRunner(
            user_eval_metric_id=user_eval_metric.id,
            is_only_eval=True,
            source="experiment",
            source_configs={"experiment_id": experiment_id},
        )
        runner.load_user_eval_metric()
        # Override dataset to use experiment's dataset for base evaluations.
        # This is critical because user_eval_metric.dataset may differ from
        # experiment.dataset, and we need to process rows from the experiment's dataset.
        runner.dataset = dataset

        # Per-row cancel check — mirrors the Celery path in
        # model_hub/tasks/user_evaluation.py:329-332. If the runner exposes
        # the callback attribute, point it at evaluation_tracker so that
        # StopUserEvalView's request_cancel breaks the loop at row level.
        if hasattr(runner, "_check_cancel_callback"):
            runner._check_cancel_callback = (
                lambda eid=user_eval_metric.id: evaluation_tracker.should_cancel(eid)
            )

        # If base column is a run_prompt column and eval needs prompt_chain,
        # attach the RunPrompter so _resolve_special_value can resolve it.
        # experiment.column is the snapshot column whose source_id points to
        # the original column. The original column's source_id is the RunPrompter ID.
        if has_prompt_chain and is_run_prompt_base:
            from model_hub.models.run_prompt import RunPrompter

            try:
                original_col = Column.objects.get(
                    id=experiment.column.source_id, deleted=False
                )
                runner.run_prompter = RunPrompter.objects.get(id=original_col.source_id)
            except (Column.DoesNotExist, RunPrompter.DoesNotExist) as exc:
                activity.logger.warning(
                    f"Could not resolve RunPrompter for base column "
                    f"{experiment.column.id} (source_id={experiment.column.source_id}): "
                    f"{exc}; prompt_chain will resolve to None"
                )

        # For dependent evals (mapping has "output"), set runner.base_column to
        # the experiment's base column so _resolve_special_value can resolve
        # "output" to the base column's cell values. We use base_column instead
        # of column because load_user_eval_metric (called inside run_prompt)
        # overwrites self.column with the eval result column.
        mapping = (user_eval_metric.config or {}).get("mapping", {})
        has_output = any(
            isinstance(v, str) and v.lower() == "output" for v in mapping.values()
        )
        if has_output and experiment.column:
            # Find the snapshot version of experiment.column
            base_col = Column.objects.filter(
                dataset=dataset,
                source_id=str(experiment.column.id),
                deleted=False,
            ).first()
            if base_col:
                runner.base_column = base_col
            else:
                # Fallback: column might be directly on snapshot
                runner.base_column = experiment.column
        column_config = runner._get_column_config(dataset)
        runner._create_or_update_column(dataset, column_config, new_column=True)

        if user_eval_metric.config.get("reason_column"):
            reason_column_name = f"{user_eval_metric.name}-reason"
            runner._create_reason_column(dataset, reason_column_name)

        # Get row IDs to process — use provided row_ids or all rows
        if row_ids:
            all_row_ids = [
                uuid.UUID(rid) if not isinstance(rid, uuid.UUID) else rid
                for rid in row_ids
            ]
        else:
            all_row_ids = list(
                Row.objects.filter(dataset=dataset, deleted=False)
                .order_by("order")
                .values_list("id", flat=True)
            )
        total_rows = len(all_row_ids)

        activity.logger.info(
            f"Running base column evaluation for {user_eval_metric.id} "
            f"on dataset {dataset.id} with {total_rows} rows in batches of {batch_size}"
        )

        # Process in batches with heartbeats
        for i in range(0, total_rows, batch_size):
            # Per-eval cancel check at batch boundary. StopUserEvalView sets
            # this flag; without the check the activity would keep writing
            # cells and overwrite the stopped state.
            if evaluation_tracker.should_cancel(user_eval_metric.id):
                activity.logger.info(
                    f"Base evaluation {eval_template_id} cancelled by user; "
                    f"processed {i}/{total_rows} rows before stop"
                )
                mark_eval_cells_stopped(
                    user_eval_metric, reason="Evaluation stopped by user"
                )
                evaluation_tracker.clear_cancel_flag(user_eval_metric.id)
                maybe_complete_experiment_after_eval_stop(experiment_id)
                return {
                    "experiment_id": experiment_id,
                    "eval_template_id": eval_template_id,
                    "status": "CANCELLED",
                }

            batch_row_ids = all_row_ids[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_rows + batch_size - 1) // batch_size

            activity.logger.info(
                f"Processing base eval batch {batch_num}/{total_batches} "
                f"({len(batch_row_ids)} rows)"
            )

            # Process this batch
            runner.run_prompt(row_ids=batch_row_ids)

            # Update heartbeat details after each batch
            # Heartbeater sends heartbeats automatically at regular intervals
            if heartbeater:
                heartbeater.details = (batch_num, total_batches)

        activity.logger.info(
            f"Completed base evaluation {eval_template_id} for all {total_rows} rows"
        )

        return {
            "experiment_id": experiment_id,
            "eval_template_id": eval_template_id,
            "status": "COMPLETED",
        }
    except Exception as e:
        activity.logger.exception(f"Error in _run_base_evaluation_sync: {e}")
        raise
    finally:
        close_old_connections()




def _check_column_status_sync(column_id: str) -> dict:
    """Synchronous implementation of check_column_status."""
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.models.develop_dataset import Column
        from model_hub.views.experiment_runner import check_and_update_column_status

        is_complete = check_and_update_column_status(uuid.UUID(column_id))
        column = Column.objects.get(id=uuid.UUID(column_id), deleted=False)

        return {
            "entity_id": column_id,
            "is_complete": is_complete,
            "status": column.status,
        }
    finally:
        close_old_connections()


def _check_experiment_status_sync(entity_id: str) -> dict:
    """Synchronous implementation of check_experiment_status."""
    close_old_connections()

    try:
        from model_hub.models.experiments import ExperimentsTable
        from model_hub.views.experiment_runner import check_and_update_experiment_status

        is_complete = check_and_update_experiment_status(uuid.UUID(entity_id))
        experiment = ExperimentsTable.objects.get(id=entity_id)

        return {
            "entity_id": entity_id,
            "is_complete": is_complete,
            "status": experiment.status,
        }
    finally:
        close_old_connections()


def _check_experiment_dataset_status_sync(entity_id: str) -> dict:
    """Synchronous implementation of check_experiment_dataset_status."""
    close_old_connections()

    try:
        from model_hub.models.experiments import ExperimentDatasetTable
        from model_hub.views.experiment_runner import (
            check_and_update_experiment_dataset_status,
        )

        is_complete = check_and_update_experiment_dataset_status(uuid.UUID(entity_id))
        experiment_dataset = ExperimentDatasetTable.objects.get(id=entity_id)

        return {
            "entity_id": entity_id,
            "is_complete": is_complete,
            "status": experiment_dataset.status,
        }
    finally:
        close_old_connections()


# =============================================================================
# Activities (async wrappers around sync functions)
# =============================================================================


@activity.defn
async def setup_experiment_activity(
    input: SetupExperimentInput,
) -> SetupExperimentOutput:
    """
    Load and setup an experiment for processing.

    - Loads experiment from database
    - Sets status to RUNNING
    - Creates/prepares evaluation columns (same as ExperimentRunner.run())
    - Returns prompt configs and eval templates to process
    Uses Heartbeater for automatic heartbeats during column creation.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _setup_experiment_sync, thread_sensitive=False
            )(input.experiment_id)

        return SetupExperimentOutput(
            experiment_id=result["experiment_id"],
            dataset_id=result["dataset_id"],
            prompt_configs=result["prompt_configs"],
            eval_template_ids=result["eval_template_ids"],
            status=result["status"],
            agent_configs=result.get("agent_configs", []),
            experiment_type=result.get("experiment_type", "llm"),
            has_base_column=result.get("has_base_column", True),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error setting up experiment {input.experiment_id}: {e}"
        )

        # Mark experiment as failed
        try:
            await otel_sync_to_async(
                _mark_experiment_failed_sync, thread_sensitive=False
            )(input.experiment_id)
        except Exception:
            pass

        raise


@activity.defn
async def process_prompt_activity(input: ProcessPromptInput) -> ProcessPromptOutput:
    """
    Process a prompt configuration for a specific model.

    - Creates ExperimentDatasetTable
    - Creates Column for results
    - Creates Cells in RUNNING state
    - Returns all info needed for row processing
    Uses Heartbeater for automatic heartbeats during bulk cell creation.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _process_prompt_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.dataset_id,
                input.prompt_config,
                input.model,
            )

        return ProcessPromptOutput(
            experiment_id=result["experiment_id"],
            column_id=result["column_id"],
            experiment_dataset_id=result["experiment_dataset_id"],
            row_ids=result["row_ids"],
            messages=result["messages"],
            model_name=result["model_name"],
            model_config=result["model_config"],
            output_format=result["output_format"],
            run_prompt_config=result["run_prompt_config"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(
            f"Error processing prompt for experiment {input.experiment_id}: {e}"
        )
        return ProcessPromptOutput(
            experiment_id=input.experiment_id,
            column_id="",
            experiment_dataset_id="",
            row_ids=[],
            messages=[],
            model_name="",
            model_config={},
            output_format=None,
            run_prompt_config=None,
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def setup_prompt_v2_activity(
    input: SetupPromptV2Input,
) -> SetupPromptV2Output:
    """
    V2: Setup a single prompt for processing.

    EDT + EPC already exist (created by V2 view).
    Creates result Column + Cells in RUNNING state.
    Returns all info needed for row processing.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _setup_prompt_v2_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.dataset_id,
                input.experiment_prompt_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
            )

        return SetupPromptV2Output(
            experiment_id=result["experiment_id"],
            experiment_prompt_config_id=result["experiment_prompt_config_id"],
            column_id=result["column_id"],
            experiment_dataset_id=result["experiment_dataset_id"],
            row_ids=result["row_ids"],
            messages=result["messages"],
            model_name=result["model_name"],
            model_config=result["model_config"],
            output_format=result["output_format"],
            run_prompt_config=result["run_prompt_config"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(
            f"Error setting up prompt V2 for experiment {input.experiment_id}: {e}"
        )
        return SetupPromptV2Output(
            experiment_id=input.experiment_id,
            experiment_prompt_config_id=input.experiment_prompt_config_id,
            column_id="",
            experiment_dataset_id="",
            row_ids=[],
            messages=[],
            model_name="",
            model_config={},
            output_format=None,
            run_prompt_config=None,
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def setup_agent_activity(
    input: SetupAgentInput,
) -> SetupAgentOutput:
    """
    V2: Setup an agent experiment entry for processing.

    EDT + EAC already exist (created by V2 view).
    Creates one result Column per LLM node + Cells in RUNNING state.
    Returns node_column_mapping + row_ids for row processing.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _setup_agent_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.dataset_id,
                input.experiment_agent_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
            )

        return SetupAgentOutput(
            experiment_id=result["experiment_id"],
            experiment_agent_config_id=result["experiment_agent_config_id"],
            experiment_dataset_id=result["experiment_dataset_id"],
            graph_version_id=result["graph_version_id"],
            node_column_mapping=result["node_column_mapping"],
            row_ids=result["row_ids"],
            status=result["status"],
            terminal_column_ids=result.get("terminal_column_ids", []),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error setting up agent for experiment {input.experiment_id}: {e}"
        )
        return SetupAgentOutput(
            experiment_id=input.experiment_id,
            experiment_agent_config_id=input.experiment_agent_config_id,
            experiment_dataset_id="",
            graph_version_id="",
            node_column_mapping={},
            row_ids=[],
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def prepare_agent_row_activity(
    input: PrepareAgentRowInput,
) -> PrepareAgentRowOutput:
    """
    Prepare a single row for graph execution.

    Reads base-data cells, builds input_payload, and creates a
    GraphExecution record via the agent_playground client helper.
    """
    try:
        result = await otel_sync_to_async(
            _prepare_agent_row_sync, thread_sensitive=False
        )(
            input.row_id,
            input.dataset_id,
            input.experiment_id,
            input.graph_version_id,
        )

        return PrepareAgentRowOutput(
            row_id=result["row_id"],
            graph_execution_id=result["graph_execution_id"],
            input_payload=result["input_payload"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Error preparing agent row {input.row_id}: {e}")
        return PrepareAgentRowOutput(
            row_id=input.row_id,
            graph_execution_id="",
            input_payload={},
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def process_row_activity(input: ProcessRowInput) -> ProcessRowOutput:
    """
    Process a single row - calls the LLM and saves the result.

    This is the core activity that does the actual LLM call.
    Also triggers row-wise evaluations via _trigger_row_wise_evaluations.
    """
    activity.logger.info(f"Processing row {input.row_id} for column {input.column_id}")

    try:
        # Use Heartbeater context manager to send periodic heartbeats while
        # the sync operation runs in a thread. This prevents heartbeat timeout errors.
        async with Heartbeater():
            result = await otel_sync_to_async(
                _process_row_sync, thread_sensitive=False
            )(
                input.row_id,
                input.column_id,
                input.dataset_id,
                input.experiment_id,
                input.messages,
                input.model,
                input.model_config,
                input.output_format,
                input.run_prompt_config,
            )

        return ProcessRowOutput(
            row_id=result["row_id"],
            column_id=result["column_id"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(f"Error processing row {input.row_id}: {e}")
        # Re-raise to let Temporal handle retries
        # ValueError is configured as non-retryable in retry policy
        raise


@activity.defn
async def run_base_evaluation_activity(
    input: RunEvaluationInput,
) -> RunEvaluationOutput:
    """
    Run base column evaluation for an experiment.

    Mirrors the logic of run_base_col_eval_task Celery task.
    Processes rows in batches with heartbeats to avoid timeout.
    """
    activity.logger.info(
        f"Starting base evaluation {input.eval_template_id} "
        f"for experiment {input.experiment_id}"
    )

    try:
        # Use Heartbeater as async context manager for automatic heartbeats
        async with Heartbeater() as heartbeater:
            result = await otel_sync_to_async(
                _run_base_evaluation_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.eval_template_id,
                row_ids=input.row_ids,
                heartbeater=heartbeater,
                batch_size=10,
            )

        activity.logger.info(
            f"Completed base evaluation {input.eval_template_id} "
            f"for experiment {input.experiment_id}"
        )

        return RunEvaluationOutput(
            experiment_id=result["experiment_id"],
            eval_template_id=result["eval_template_id"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(
            f"Error running evaluation {input.eval_template_id}: {e}"
        )
        return RunEvaluationOutput(
            experiment_id=input.experiment_id,
            eval_template_id=input.eval_template_id,
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def check_column_status_activity(
    input: CheckStatusInput,
) -> CheckStatusOutput:
    """
    Check and update column status.
    """
    try:
        result = await otel_sync_to_async(
            _check_column_status_sync, thread_sensitive=False
        )(input.entity_id)

        return CheckStatusOutput(
            entity_id=result["entity_id"],
            is_complete=result["is_complete"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(f"Error checking column status: {e}")
        return CheckStatusOutput(
            entity_id=input.entity_id,
            is_complete=False,
            status="ERROR",
        )


@activity.defn
async def check_experiment_status_activity(
    input: CheckStatusInput,
) -> CheckStatusOutput:
    """
    Check and update experiment status.
    """
    try:
        result = await otel_sync_to_async(
            _check_experiment_status_sync, thread_sensitive=False
        )(input.entity_id)

        return CheckStatusOutput(
            entity_id=result["entity_id"],
            is_complete=result["is_complete"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(f"Error checking experiment status: {e}")
        return CheckStatusOutput(
            entity_id=input.entity_id,
            is_complete=False,
            status="ERROR",
        )


@activity.defn
async def check_experiment_dataset_status_activity(
    input: CheckStatusInput,
) -> CheckStatusOutput:
    """
    Check and update experiment_dataset status.
    This triggers the cascade: experiment_dataset → experiment status updates.
    """
    try:
        result = await otel_sync_to_async(
            _check_experiment_dataset_status_sync, thread_sensitive=False
        )(input.entity_id)

        return CheckStatusOutput(
            entity_id=result["entity_id"],
            is_complete=result["is_complete"],
            status=result["status"],
        )

    except Exception as e:
        activity.logger.exception(f"Error checking experiment_dataset status: {e}")
        return CheckStatusOutput(
            entity_id=input.entity_id,
            is_complete=False,
            status="ERROR",
        )


@activity.defn
async def mark_experiment_failed_activity(experiment_id: str) -> None:
    """Mark an experiment as failed."""
    try:
        await otel_sync_to_async(_mark_experiment_failed_sync, thread_sensitive=False)(
            experiment_id
        )
        activity.logger.info(f"Marked experiment {experiment_id} as FAILED")
    except Exception as e:
        activity.logger.exception(
            f"Error marking experiment {experiment_id} as failed: {e}"
        )
        raise


@activity.defn
async def mark_experiment_running_activity(
    input: MarkExperimentRunningInput,
) -> MarkExperimentRunningOutput:
    """Mark experiment RUNNING without resetting eval columns."""
    try:
        result = await otel_sync_to_async(
            _mark_experiment_running_sync, thread_sensitive=False
        )(input.experiment_id)

        return MarkExperimentRunningOutput(
            experiment_id=result["experiment_id"],
            dataset_id=result["dataset_id"],
            eval_template_ids=result["eval_template_ids"],
            status=result["status"],
            prompt_configs=result.get("prompt_configs", []),
            agent_configs=result.get("agent_configs", []),
            has_base_column=result.get("has_base_column", True),
        )
    except Exception as e:
        activity.logger.exception(
            f"Error marking experiment {input.experiment_id} running: {e}"
        )
        return MarkExperimentRunningOutput(
            experiment_id=input.experiment_id,
            dataset_id="",
            eval_template_ids=[],
            status="FAILED",
            error=str(e),
        )


@activity.defn
async def cleanup_running_cells_activity(
    input: CleanupRunningCellsInput,
) -> CleanupRunningCellsOutput:
    """
    Mark all running cells as error for an experiment.

    This is called when a workflow fails or is terminated to cleanup
    cells that were left in 'running' state.
    Uses Heartbeater for automatic heartbeats during bulk cell updates.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _cleanup_running_cells_sync, thread_sensitive=False
            )(input.experiment_id)

        return CleanupRunningCellsOutput(
            experiment_id=result["experiment_id"],
            cells_cleaned=result["cells_cleaned"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error in cleanup_running_cells_activity for {input.experiment_id}: {e}"
        )
        return CleanupRunningCellsOutput(
            experiment_id=input.experiment_id,
            cells_cleaned=0,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Get Eval Templates Activity
# =============================================================================


def _get_eval_templates_sync(column_id: str, experiment_id: str) -> dict:
    """Synchronous implementation of get_eval_templates."""
    close_old_connections()

    try:
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Column
        from model_hub.models.experiments import ExperimentsTable

        column = Column.objects.get(id=uuid.UUID(column_id), deleted=False)

        # Only process for experiment columns
        if column.source != SourceChoices.EXPERIMENT.value or not column.source_id:
            return {
                "eval_template_ids": [],
            }

        # Use the provided experiment_id directly instead of reverse relationship lookup
        experiment = ExperimentsTable.objects.prefetch_related(
            "user_eval_template_ids"
        ).get(id=uuid.UUID(experiment_id), deleted=False)

        eval_template_ids = list(
            experiment.user_eval_template_ids.values_list("id", flat=True)
        )

        return {
            "eval_template_ids": [str(eid) for eid in eval_template_ids],
        }

    except Exception as e:
        activity.logger.exception(f"Error in _get_eval_templates_sync: {e}")
        return {
            "eval_template_ids": [],
        }
    finally:
        close_old_connections()


@activity.defn
async def get_eval_templates_activity(
    input: GetEvalTemplatesInput,
) -> GetEvalTemplatesOutput:
    """
    Get eval template IDs for an experiment.
    Returns list of eval template IDs associated with the experiment.
    """
    try:
        result = await otel_sync_to_async(
            _get_eval_templates_sync, thread_sensitive=False
        )(
            input.column_id,
            input.experiment_id,
        )

        return GetEvalTemplatesOutput(
            eval_template_ids=result["eval_template_ids"],
        )

    except Exception as e:
        activity.logger.exception(f"Error in get_eval_templates_activity: {e}")
        return GetEvalTemplatesOutput(
            eval_template_ids=[],
        )


# =============================================================================
# Batch Template Eval Activity
# =============================================================================


def _process_batch_template_eval_sync(
    row_ids: list,
    column_id: str,
    dataset_id: str,
    experiment_id: str,
    eval_template_id: str,
) -> dict:
    """
    Process a batch of rows for a single eval template.
    Processes all rows in the batch for the specified template.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Column
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )
        from model_hub.services.experiment_utils import is_experiment_cancelled
        from model_hub.views.eval_runner import EvaluationRunner

        # Guard: skip entirely if experiment was already cancelled.
        # This catches fire-and-forget eval activities that were scheduled
        # via workflow.start_activity() before the workflow was cancelled.
        if is_experiment_cancelled(uuid.UUID(experiment_id)):
            return {
                "row_ids": row_ids,
                "column_id": column_id,
                "eval_template_id": eval_template_id,
                "status": "SKIPPED",
            }

        column = Column.objects.select_related("dataset").get(
            id=uuid.UUID(column_id), deleted=False
        )

        # Only process for experiment columns
        if column.source != SourceChoices.EXPERIMENT.value or not column.source_id:
            return {
                "row_ids": row_ids,
                "column_id": column_id,
                "eval_template_id": eval_template_id,
                "status": "SKIPPED",
            }

        # Use the provided experiment_id directly instead of reverse relationship lookup
        experiment = ExperimentsTable.objects.select_related("snapshot_dataset").get(
            id=uuid.UUID(experiment_id), deleted=False
        )

        experiment_dataset = ExperimentDatasetTable.objects.get(id=column.source_id)
        eval_template = UserEvalMetric.objects.select_related("template").get(
            id=uuid.UUID(eval_template_id)
        )

        config = {
            "dataset_id": str(experiment_dataset.id),
            "input": str(column.id),
            "experiment_id": str(experiment.id),
            "source": "experiment",
        }

        runner = EvaluationRunner(
            user_eval_metric_id=eval_template.id,
            experiment_dataset=experiment_dataset,
            is_only_eval=False,
            source="experiment",
            source_id=str(eval_template.template.id),
            source_configs=config,
            column=column,
        )

        # Set dataset explicitly to experiment's snapshot dataset
        runner.dataset = experiment.snapshot_dataset

        # Run evaluation for the batch of rows
        runner.run_prompt(row_ids=row_ids)

        return {
            "row_ids": row_ids,
            "column_id": column_id,
            "eval_template_id": eval_template_id,
            "status": "COMPLETED",
        }

    except Exception as e:
        activity.logger.exception(
            f"Error processing batch eval for rows {row_ids}, template {eval_template_id}: {e}"
        )
        return {
            "row_ids": row_ids,
            "column_id": column_id,
            "eval_template_id": eval_template_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def process_batch_template_eval_activity(
    input: ProcessBatchEvalInput,
) -> ProcessBatchEvalOutput:
    """
    Process a batch of rows for a single eval template.
    Processes all rows in the batch for the specified template.
    """
    activity.logger.info(
        f"Processing batch eval for {len(input.row_ids)} rows, template {input.eval_template_id}"
    )

    try:
        # Use Heartbeater context manager to send periodic heartbeats while
        # the sync operation runs in a thread. This prevents heartbeat timeout errors.
        async with Heartbeater():
            result = await otel_sync_to_async(
                _process_batch_template_eval_sync, thread_sensitive=False
            )(
                input.row_ids,
                input.column_id,
                input.dataset_id,
                input.experiment_id,
                input.eval_template_id,
            )

        return ProcessBatchEvalOutput(
            row_ids=result["row_ids"],
            column_id=result["column_id"],
            eval_template_id=result["eval_template_id"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Error in process_batch_template_eval_activity: {e}")
        return ProcessBatchEvalOutput(
            row_ids=input.row_ids,
            column_id=input.column_id,
            eval_template_id=input.eval_template_id,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Error Eval Cells Activity (for upstream prompt failures)
# =============================================================================


def _create_error_eval_cells_sync(
    failed_row_ids: list,
    column_id: str,
    dataset_id: str,
    experiment_id: str,
    eval_template_ids: list,
    error_message: str,
) -> dict:
    """
    Create eval cells in ERROR state for rows where the upstream prompt failed.

    When a prompt fails for a row, eval activities are never started and no eval
    cells are created. This leaves the frontend showing "loading" for those eval
    cells. This activity creates them in ERROR state with a descriptive message.

    Reuses EvaluationRunner's column creation pattern to find/create the correct
    eval column, ensuring consistency with normal eval processing.
    """
    close_old_connections()
    cells_created = 0

    try:
        import json

        from model_hub.models.choices import CellStatus, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )
        from model_hub.views.eval_runner import EvaluationRunner

        column = Column.objects.select_related("dataset").get(
            id=uuid.UUID(column_id), deleted=False
        )

        # Only process for experiment columns
        if column.source != SourceChoices.EXPERIMENT.value or not column.source_id:
            return {
                "cells_created": 0,
                "status": "SKIPPED",
            }

        experiment = ExperimentsTable.objects.select_related("snapshot_dataset").get(
            id=uuid.UUID(experiment_id), deleted=False
        )
        experiment_dataset = ExperimentDatasetTable.objects.get(id=column.source_id)
        dataset = experiment.snapshot_dataset

        error_value = json.dumps({"error": error_message})

        for eval_template_id in eval_template_ids:
            try:
                eval_template = UserEvalMetric.objects.select_related("template").get(
                    id=uuid.UUID(eval_template_id)
                )

                config = {
                    "dataset_id": str(experiment_dataset.id),
                    "input": str(column.id),
                    "experiment_id": str(experiment.id),
                    "source": "experiment",
                }

                runner = EvaluationRunner(
                    user_eval_metric_id=eval_template.id,
                    experiment_dataset=experiment_dataset,
                    is_only_eval=False,
                    source="experiment",
                    source_id=str(eval_template.template.id),
                    source_configs=config,
                    column=column,
                )
                runner.dataset = dataset
                runner.load_user_eval_metric()

                column_config = runner._get_column_config(dataset)
                eval_column = runner._create_or_update_column(
                    dataset, column_config, new_column=True
                )

                # Bulk fetch all rows for this batch
                row_uuids = [uuid.UUID(rid) for rid in failed_row_ids]
                rows = Row.objects.filter(
                    id__in=row_uuids, dataset=dataset, deleted=False
                )
                row_map = {str(r.id): r for r in rows}

                missing = set(failed_row_ids) - set(row_map.keys())
                for rid in missing:
                    activity.logger.warning(
                        f"Row {rid} not found in dataset {dataset.id}"
                    )

                # Stop guard: skip writing error cells if the user already
                # stopped this eval — otherwise the late error path
                # overwrites the "User stopped evaluation" marker.
                from model_hub.services.experiment_utils import (
                    is_user_eval_stopped,
                )

                if is_user_eval_stopped(eval_template.id):
                    continue

                # Bulk create error cells for the eval column
                error_value_infos = json.dumps({"reason": error_message})
                eval_cells = [
                    Cell(
                        dataset=dataset,
                        column=eval_column,
                        row=row,
                        value=error_value,
                        status=CellStatus.ERROR.value,
                        value_infos=error_value_infos,
                    )
                    for row in row_map.values()
                ]
                if eval_cells:
                    Cell.objects.bulk_create(eval_cells, ignore_conflicts=True)
                    cells_created += len(eval_cells)

                # Handle reason column if enabled
                if eval_template.config.get("reason_column"):
                    reason_column_name = f"{eval_template.name}-{column.name}-reason"
                    reason_column = runner._create_reason_column(
                        dataset, reason_column_name, parent_column=eval_column
                    )
                    if reason_column is not None:
                        reason_cells = [
                            Cell(
                                dataset=dataset,
                                column=reason_column,
                                row=row,
                                value=error_message,
                                status=CellStatus.ERROR.value,
                                value_infos=json.dumps({"reason": error_message}),
                            )
                            for row in row_map.values()
                        ]
                        if reason_cells:
                            Cell.objects.bulk_create(reason_cells, ignore_conflicts=True)

            except Exception as e:
                activity.logger.exception(
                    f"Error creating error eval cells for template "
                    f"{eval_template_id}: {e}"
                )

        return {
            "cells_created": cells_created,
            "status": "COMPLETED",
        }

    except Exception as e:
        activity.logger.exception(f"Error in _create_error_eval_cells_sync: {e}")
        return {
            "cells_created": cells_created,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def create_error_eval_cells_activity(
    input: CreateErrorEvalCellsInput,
) -> CreateErrorEvalCellsOutput:
    """Create eval cells in ERROR state for rows where upstream prompt failed."""
    activity.logger.info(
        f"Creating error eval cells for {len(input.failed_row_ids)} failed rows, "
        f"{len(input.eval_template_ids)} eval templates"
    )

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _create_error_eval_cells_sync, thread_sensitive=False
            )(
                input.failed_row_ids,
                input.column_id,
                input.dataset_id,
                input.experiment_id,
                input.eval_template_ids,
                input.error_message,
            )

        return CreateErrorEvalCellsOutput(
            cells_created=result["cells_created"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Error in create_error_eval_cells_activity: {e}")
        return CreateErrorEvalCellsOutput(
            cells_created=0,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Error Agent Cells Activity
# =============================================================================


def _create_error_agent_cells_sync(
    row_id: str,
    dataset_id: str,
    experiment_id: str,
    node_column_mapping: dict,
    error_message: str,
) -> dict:
    """Create agent output cells in ERROR state when graph-level failure occurs.

    Unlike eval cells, agent columns already exist. We just need to write
    error cells for any columns that didn't get written by node sinks.
    """
    close_old_connections()
    cells_created = 0

    try:
        import json

        from model_hub.models.choices import CellStatus
        from model_hub.models.develop_dataset import Cell

        for _node_id, column_id in node_column_mapping.items():
            # Only write if cell is still in RUNNING state (not already
            # written by a node-level sink)
            existing = Cell.objects.filter(
                column_id=column_id, row_id=row_id, dataset_id=dataset_id
            ).first()
            if existing and existing.status != CellStatus.RUNNING.value:
                continue

            Cell.objects.update_or_create(
                column_id=column_id,
                row_id=row_id,
                dataset_id=dataset_id,
                defaults={
                    "value": error_message,
                    "value_infos": json.dumps({"reason": error_message}),
                    "status": CellStatus.ERROR.value,
                    "deleted": False,
                },
            )
            cells_created += 1

        return {"cells_created": cells_created, "status": "SUCCESS"}
    except Exception as e:
        activity.logger.exception(f"Failed to create error agent cells: {e}")
        return {"cells_created": cells_created, "status": "FAILED", "error": str(e)}
    finally:
        close_old_connections()


@activity.defn
async def create_error_agent_cells_activity(
    input: CreateErrorAgentCellsInput,
) -> CreateErrorAgentCellsOutput:
    """Create agent output cells in ERROR state when graph execution failed."""
    activity.logger.info(
        f"Creating error agent cells for row {input.row_id}, "
        f"{len(input.node_column_mapping)} columns"
    )

    try:
        result = await otel_sync_to_async(
            _create_error_agent_cells_sync, thread_sensitive=False
        )(
            input.row_id,
            input.dataset_id,
            input.experiment_id,
            input.node_column_mapping,
            input.error_message,
        )

        return CreateErrorAgentCellsOutput(
            cells_created=result["cells_created"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Error in create_error_agent_cells_activity: {e}")
        return CreateErrorAgentCellsOutput(
            cells_created=0,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Row-wise Evaluation Activity
# =============================================================================


def _process_row_evals_sync(
    row_id: str,
    column_id: str,
    dataset_id: str,
    experiment_id: str,
) -> dict:
    """
    Process row-wise evaluations for a single row.
    Mirrors the logic from _trigger_row_wise_evaluations but runs synchronously
    instead of spawning Celery tasks.
    """
    close_old_connections()
    evals_triggered = 0

    try:
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Column
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )
        from model_hub.views.eval_runner import EvaluationRunner
        from tfc.utils.distributed_state import evaluation_tracker

        column = Column.objects.get(id=uuid.UUID(column_id), deleted=False)

        # Only process for experiment columns
        if column.source != SourceChoices.EXPERIMENT.value or not column.source_id:
            return {
                "row_id": row_id,
                "column_id": column_id,
                "evals_triggered": 0,
                "status": "SKIPPED",
            }

        experiment_dataset = ExperimentDatasetTable.objects.get(id=column.source_id)

        experiment = ExperimentsTable.objects.select_related("snapshot_dataset").get(
            id=uuid.UUID(experiment_id), deleted=False
        )

        eval_templates = list(experiment.user_eval_template_ids.all())
        if not eval_templates:
            return {
                "row_id": row_id,
                "column_id": column_id,
                "evals_triggered": 0,
                "status": "SKIPPED",
            }

        # Process each eval template
        for eval_template in eval_templates:
            # Per-eval cancel check — StopUserEvalView signals cancel via
            # evaluation_tracker. Skip this eval's row without running the
            # runner so we don't write a "No reasoning available" error cell
            # over the stopped marker.
            if evaluation_tracker.should_cancel(eval_template.id):
                activity.logger.info(
                    f"Skipping eval {eval_template.id} on row {row_id}: "
                    f"cancel requested by user"
                )
                continue

            try:
                config = {
                    "dataset_id": str(experiment_dataset.id),
                    "input": str(column.id),
                    "experiment_id": str(experiment.id),
                    "source": "experiment",
                }

                runner = EvaluationRunner(
                    user_eval_metric_id=eval_template.id,
                    experiment_dataset=experiment_dataset,
                    is_only_eval=False,
                    source="experiment",
                    source_id=str(eval_template.template.id),
                    source_configs=config,
                    column=column,
                )

                # Set dataset explicitly to experiment's snapshot dataset
                runner.dataset = experiment.snapshot_dataset

                # Wire distributed cancel check so the runner aborts the
                # row mid-flight if the user presses Stop during execution.
                if hasattr(runner, "_check_cancel_callback"):
                    runner._check_cancel_callback = (
                        lambda eid=eval_template.id: (
                            evaluation_tracker.should_cancel(eid)
                        )
                    )

                # Run evaluation for this single row
                runner.run_prompt(row_ids=[row_id])
                evals_triggered += 1

            except Exception as eval_error:
                activity.logger.exception(
                    f"Error processing eval for row {row_id}, template {eval_template.id}: {eval_error}"
                )

        return {
            "row_id": row_id,
            "column_id": column_id,
            "evals_triggered": evals_triggered,
            "status": "COMPLETED",
        }

    except Exception as e:
        activity.logger.exception(f"Error in _process_row_evals_sync: {e}")
        return {
            "row_id": row_id,
            "column_id": column_id,
            "evals_triggered": evals_triggered,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def process_row_eval_activity(input: ProcessRowEvalInput) -> ProcessRowEvalOutput:
    """
    Process row-wise evaluations for a single row.
    Called after process_row_activity completes successfully.
    Uses Heartbeater for automatic heartbeats during long-running evaluations.
    """
    activity.logger.info(f"Processing evals for row {input.row_id}")

    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _process_row_evals_sync, thread_sensitive=False
            )(
                input.row_id,
                input.column_id,
                input.dataset_id,
                input.experiment_id,
            )

        return ProcessRowEvalOutput(
            row_id=result["row_id"],
            column_id=result["column_id"],
            evals_triggered=result["evals_triggered"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(f"Error in process_row_eval_activity: {e}")
        return ProcessRowEvalOutput(
            row_id=input.row_id,
            column_id=input.column_id,
            evals_triggered=0,
            status="FAILED",
            error=str(e),
        )


# =============================================================================
# Dependency Analysis Activity
# =============================================================================


def _analyze_dependencies_sync(
    experiment_id: str,
    eval_template_ids: list,
) -> dict:
    """
    Analyze eval template mappings to determine which evals depend on prompt context.

    Inspects each eval template's config.mapping values:
    - If ANY value is 'output' or 'prompt_chain' → dependent
      ('output' needs LLM response; 'prompt_chain' needs experiment_dataset
       context which is only available in per-EDT child workflows)
    - Otherwise → independent (can run in parallel with prompts)
    """
    close_old_connections()

    try:
        from model_hub.models.evals_metric import UserEvalMetric

        independent = []
        dependent = []

        eval_templates = UserEvalMetric.objects.filter(
            id__in=[uuid.UUID(eid) for eid in eval_template_ids]
        )

        for et in eval_templates:
            mapping = {}
            if et.config and isinstance(et.config, dict):
                mapping = et.config.get("mapping", {})

            needs_edt_context = any(
                isinstance(v, str) and v.lower() in ("output", "prompt_chain")
                for v in mapping.values()
            )

            if needs_edt_context:
                dependent.append(str(et.id))
            else:
                independent.append(str(et.id))

        activity.logger.info(
            f"Dependency analysis for experiment {experiment_id}: "
            f"{len(independent)} independent, {len(dependent)} dependent evals"
        )

        return {
            "independent_eval_ids": independent,
            "dependent_eval_ids": dependent,
        }
    except Exception as e:
        activity.logger.exception(f"Error in _analyze_dependencies_sync: {e}")
        # On error, treat all as dependent (safe default — waits for output)
        return {
            "independent_eval_ids": [],
            "dependent_eval_ids": eval_template_ids,
        }
    finally:
        close_old_connections()


@activity.defn
async def analyze_dependencies_activity(
    input: AnalyzeDependenciesInput,
) -> AnalyzeDependenciesOutput:
    """
    Analyze eval template dependencies to determine execution order.

    Returns which evals can run independently (in parallel with prompts)
    and which need to wait for prompt output (run per-row after prompt).
    """
    try:
        result = await otel_sync_to_async(
            _analyze_dependencies_sync, thread_sensitive=False
        )(
            input.experiment_id,
            input.eval_template_ids,
        )

        return AnalyzeDependenciesOutput(
            independent_eval_ids=result["independent_eval_ids"],
            dependent_eval_ids=result["dependent_eval_ids"],
        )

    except Exception as e:
        activity.logger.exception(f"Error in analyze_dependencies_activity: {e}")
        return AnalyzeDependenciesOutput(
            independent_eval_ids=[],
            dependent_eval_ids=input.eval_template_ids,
        )


def _resolve_edt_columns_sync(experiment_id: str, edt_ids: list = None) -> dict:
    """
    Resolve EDT output columns for an experiment.

    Finds the output Column for each EDT in the snapshot dataset
    (Column.source_id == EDT.id), plus all row IDs.

    If edt_ids is provided, only returns columns for those specific EDTs.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Column, Row
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )

        experiment = ExperimentsTable.objects.select_related("snapshot_dataset").get(
            id=uuid.UUID(experiment_id), deleted=False
        )

        dataset = experiment.snapshot_dataset

        # Use provided EDT IDs or fetch all for this experiment
        if edt_ids:
            filtered_edt_ids = edt_ids
        else:
            filtered_edt_ids = [
                str(eid)
                for eid in ExperimentDatasetTable.objects.filter(
                    experiment=experiment, deleted=False
                ).values_list("id", flat=True)
            ]

        # Find output columns: source=EXPERIMENT, source_id=EDT.id
        edt_column_ids = list(
            Column.objects.filter(
                dataset=dataset,
                source=SourceChoices.EXPERIMENT.value,
                source_id__in=filtered_edt_ids,
                deleted=False,
            ).values_list("id", flat=True)
        )

        row_ids = list(
            Row.objects.filter(dataset=dataset, deleted=False)
            .order_by("order")
            .values_list("id", flat=True)
        )

        return {
            "edt_column_ids": [str(cid) for cid in edt_column_ids],
            "row_ids": [str(rid) for rid in row_ids],
            "dataset_id": str(dataset.id),
        }
    except Exception as e:
        activity.logger.exception(f"Error in _resolve_edt_columns_sync: {e}")
        raise
    finally:
        close_old_connections()


@activity.defn
async def resolve_edt_columns_activity(
    input: ResolveEdtColumnsInput,
) -> ResolveEdtColumnsOutput:
    """Resolve EDT output columns for eval-only re-run."""
    result = await otel_sync_to_async(
        _resolve_edt_columns_sync, thread_sensitive=False
    )(input.experiment_id, edt_ids=input.edt_ids)

    return ResolveEdtColumnsOutput(
        edt_column_ids=result["edt_column_ids"],
        row_ids=result["row_ids"],
        dataset_id=result["dataset_id"],
    )


def _stop_experiment_cleanup_sync(experiment_id: str, stop_message: str) -> dict:
    """
    Cleanup DB state when an experiment is stopped by user.

    Marks all RUNNING cells as ERROR with the stop message,
    RUNNING columns and EDTs as FAILED, and experiment as CANCELLED.
    """
    close_old_connections()

    try:
        from model_hub.models.choices import CellStatus, SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )

        experiment = ExperimentsTable.objects.get(
            id=uuid.UUID(experiment_id), deleted=False
        )

        # Get columns directly from the snapshot dataset
        all_column_ids = list(
            Column.objects.filter(
                dataset=experiment.snapshot_dataset,
                deleted=False,
            ).values_list("id", flat=True)
        )

        cells_updated = 0
        if all_column_ids:
            # Mark RUNNING cells as ERROR with stop message
            cells_updated = Cell.objects.filter(
                column_id__in=all_column_ids,
                status=CellStatus.RUNNING.value,
                deleted=False,
            ).update(
                status=CellStatus.ERROR.value,
                value=stop_message,
                value_infos={"reason": stop_message},
            )

            # Mark RUNNING columns as FAILED
            Column.objects.filter(
                id__in=all_column_ids,
                status=StatusType.RUNNING.value,
            ).update(status=StatusType.FAILED.value)

        # Create missing ERROR cells for experiment-generated columns.
        # Some columns (especially eval columns) may not have cells for all
        # rows if the experiment was cancelled before those activities ran.
        EXPERIMENT_SOURCES = [
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            SourceChoices.EVALUATION.value,
            SourceChoices.EVALUATION_REASON.value,
            SourceChoices.EVALUATION_TAGS.value,
        ]

        experiment_column_ids = list(
            Column.objects.filter(
                dataset=experiment.snapshot_dataset,
                deleted=False,
                source__in=EXPERIMENT_SOURCES,
            ).values_list("id", flat=True)
        )

        cells_created = 0
        if experiment_column_ids:
            row_ids = list(
                Row.objects.filter(
                    dataset=experiment.snapshot_dataset,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            if row_ids:
                existing_pairs = set(
                    Cell.objects.filter(
                        column_id__in=experiment_column_ids,
                        dataset=experiment.snapshot_dataset,
                        deleted=False,
                    ).values_list("row_id", "column_id")
                )

                BATCH_SIZE = 5000
                cells_to_create = []

                for col_id in experiment_column_ids:
                    for row_id in row_ids:
                        if (row_id, col_id) not in existing_pairs:
                            cells_to_create.append(
                                Cell(
                                    dataset=experiment.snapshot_dataset,
                                    column_id=col_id,
                                    row_id=row_id,
                                    status=CellStatus.ERROR.value,
                                    value=stop_message,
                                    value_infos={"reason": stop_message},
                                )
                            )
                            if len(cells_to_create) >= BATCH_SIZE:
                                Cell.objects.bulk_create(
                                    cells_to_create,
                                    batch_size=BATCH_SIZE,
                                    ignore_conflicts=True,
                                )
                                cells_created += len(cells_to_create)
                                cells_to_create = []

                if cells_to_create:
                    Cell.objects.bulk_create(
                        cells_to_create, batch_size=BATCH_SIZE, ignore_conflicts=True
                    )
                    cells_created += len(cells_to_create)

        # Mark RUNNING/QUEUED EDTs as FAILED
        ExperimentDatasetTable.objects.filter(
            experiment=experiment,
            deleted=False,
            status__in=[
                StatusType.RUNNING.value,
                StatusType.QUEUED.value,
            ],
        ).update(status=StatusType.FAILED.value)

        # Mark experiment as CANCELLED
        ExperimentsTable.objects.filter(id=experiment.id).update(
            status=StatusType.CANCELLED.value
        )

        activity.logger.info(
            f"Stop cleanup for experiment {experiment_id}: "
            f"{cells_updated} cells marked as ERROR, "
            f"{cells_created} missing cells created as ERROR"
        )

        return {
            "experiment_id": experiment_id,
            "cells_cleaned": cells_updated + cells_created,
            "status": "COMPLETED",
        }

    except Exception as e:
        activity.logger.exception(f"Error in _stop_experiment_cleanup_sync: {e}")
        return {
            "experiment_id": experiment_id,
            "cells_cleaned": 0,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def stop_experiment_cleanup_activity(
    input: StopExperimentCleanupInput,
) -> StopExperimentCleanupOutput:
    """
    Cleanup activity for stopped experiments.

    Called from workflow CancelledError handlers. Marks RUNNING cells as ERROR,
    columns/EDTs as FAILED, and experiment as CANCELLED.
    """
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _stop_experiment_cleanup_sync, thread_sensitive=False
            )(input.experiment_id, input.stop_message)

        return StopExperimentCleanupOutput(
            experiment_id=result["experiment_id"],
            cells_cleaned=result["cells_cleaned"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error in stop_experiment_cleanup_activity for "
            f"{input.experiment_id}: {e}"
        )
        return StopExperimentCleanupOutput(
            experiment_id=input.experiment_id,
            cells_cleaned=0,
            status="FAILED",
            error=str(e),
        )


# Export all activities and types
__all__ = [
    # Types (re-exported from types.py)
    "ProcessRowInput",
    "ProcessRowOutput",
    "SetupExperimentInput",
    "SetupExperimentOutput",
    "ProcessPromptInput",
    "ProcessPromptOutput",
    "RunEvaluationInput",
    "RunEvaluationOutput",
    "CheckStatusInput",
    "CheckStatusOutput",
    "ProcessRowEvalInput",
    "ProcessRowEvalOutput",
    "GetEvalTemplatesInput",
    "GetEvalTemplatesOutput",
    "ProcessBatchEvalInput",
    "ProcessBatchEvalOutput",
    "CleanupRunningCellsInput",
    "CleanupRunningCellsOutput",
    # V2 types
    "SetupPromptV2Input",
    "SetupPromptV2Output",
    "SetupAgentInput",
    "SetupAgentOutput",
    "PrepareAgentRowInput",
    "PrepareAgentRowOutput",
    "AnalyzeDependenciesInput",
    "AnalyzeDependenciesOutput",
    "ResolveEdtColumnsInput",
    "ResolveEdtColumnsOutput",
    # Rerun cells types
    "MarkExperimentRunningInput",
    "MarkExperimentRunningOutput",
    # Activities
    "setup_experiment_activity",
    "process_prompt_activity",
    "setup_prompt_v2_activity",
    "setup_agent_activity",
    "prepare_agent_row_activity",
    "process_row_activity",
    "process_row_eval_activity",
    "get_eval_templates_activity",
    "process_batch_template_eval_activity",
    "run_base_evaluation_activity",
    "check_column_status_activity",
    "check_experiment_status_activity",
    "mark_experiment_failed_activity",
    "mark_experiment_running_activity",
    "cleanup_running_cells_activity",
    "analyze_dependencies_activity",
    "resolve_edt_columns_activity",
    "stop_experiment_cleanup_activity",
]
