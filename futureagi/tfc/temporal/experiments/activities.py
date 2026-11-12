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
    HttpRowInput,
    HttpSourceInput,
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


def _setup_experiment_sync(experiment_id: str, http_admission_id: str = "") -> dict:
    """Synchronous implementation of setup_experiment (V2: EPC/EAC based)."""
    close_old_connections()

    try:
        from model_hub.models.choices import StatusType
        from model_hub.views.experiment_runner import ExperimentRunner

        runner = ExperimentRunner(uuid.UUID(experiment_id))
        runner.load_experiment()

        from model_hub.services.http_experiment_source import prepare_http_experiment

        if not prepare_http_experiment(runner, http_admission_id):
            runner.experiment.status = StatusType.RUNNING.value
            runner.experiment.save(update_fields=["status"])
            runner.empty_or_create_evals_column()

        # V2: Get prompt/agent config IDs
        prompt_config_ids, agent_config_ids = _get_config_ids(runner.experiment)
        from model_hub.services.http_experiment_source import http_source_ids

        http_sources = http_source_ids(runner.experiment)
        from model_hub.services.experiment_submit import state_of

        http_plan = state_of(runner.experiment).get("http_plan")
        if http_plan:
            # This admission owns only the captured HTTP source/row plan.
            http_sources = list(http_plan["targets"])
            prompt_config_ids, agent_config_ids = [], []

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
            "http_sources": http_sources,
            "experiment_type": runner.experiment.experiment_type,
            "has_base_column": runner.experiment.column_id is not None
            and not http_plan,
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

        ExperimentsTable.objects.filter(id=experiment_id).exclude(
            status=StatusType.CANCELLED.value,
        ).update(status=StatusType.FAILED.value)
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
        from model_hub.services.http_experiment_source import http_source_ids

        http_sources = http_source_ids(experiment)

        return {
            "experiment_id": str(experiment.id),
            "dataset_id": str(experiment.snapshot_dataset_id),
            "prompt_configs": [str(pc_id) for pc_id in prompt_config_ids],
            "agent_configs": [str(ac_id) for ac_id in agent_config_ids],
            "http_sources": http_sources,
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


def _cleanup_running_cells_sync(experiment_id: str, *, manage_connections=True) -> dict:
    """
    Mark all running cells as error for a SPECIFIC experiment only.

    This is used to cleanup cells that were left in 'running' state
    when a workflow was terminated or failed unexpectedly.

    IMPORTANT: Only cleans up cells belonging to THIS experiment's columns,
    not all experiment columns in the dataset. This allows multiple experiments
    on the same dataset to run concurrently without interfering with each other.
    """
    if manage_connections:
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
        if manage_connections:
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

        from model_hub.models.choices import CellStatus, StatusType
        from model_hub.models.develop_dataset import Row
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


def _setup_native_prompt(
    experiment_id, dataset_id, config_id, context, rerun_row_ids=None, failed_only=False
):
    """Prepare exactly the rows of this config's admitted immutable plan.

    A plan-bound context resolves its rows from the canonical pinned run, which
    the persisted plan was validated against; mutable selectors (failed-only,
    caller rows) can only confirm that plan, never widen or change it.
    """
    from model_hub.models.choices import StatusType
    from model_hub.models.experiments import ExperimentPromptConfig
    from model_hub.services.experiment_run_writes import RunWriteError, _planned
    from model_hub.services.native_run_binding import snapshot_examples
    from model_hub.services.native_run_recording import prompt_execution_config
    from model_hub.views.experiment_runner import _native_dispatch

    if failed_only or (rerun_row_ids and not context.get("plan_id")):
        raise ValueError("native create cannot use mutable rerun selectors")
    dataset, run, binding = _native_dispatch(
        experiment_id,
        context,
        dataset_id=dataset_id,
        full_plan=True,
    )
    if str(binding.native_config_id) != str(config_id):
        raise RunWriteError("native prompt configuration does not match dispatch")
    if run.status in {"draft", "cancelled"}:
        raise RunWriteError("native execution is not confirmed or was cancelled")
    config = prompt_execution_config(dataset=dataset, run_id=run.id)
    eval_only = run.metadata["native_identity"].get("execution_mode") == "eval_only"
    from model_hub.views.eval_runner import NativeEvaluationRunner

    evaluators = {
        pin["metric_id"]: NativeEvaluationRunner(
            dataset=dataset, run=run, binding=binding, pin=pin
        )
        for pin in run.evaluator_versions
    }
    # Audit exactly the checked effective values, never a second live resolver.
    if not eval_only:
        ExperimentPromptConfig.no_workspace_objects.filter(id=config_id).update(
            model_params=config["model_params"],
            configuration=config["configuration"],
        )
    planned = _planned(run)
    links = [
        link
        for link in snapshot_examples(binding.snapshot)
        if (str(link.version_item.example_id), 1) in planned
    ]
    rows = [str(link.snapshot_row_id) for link in links]
    if rerun_row_ids and {str(value) for value in rerun_row_ids} != set(rows):
        raise RunWriteError("native setup rows do not match the admitted plan")
    if run.completed_at is None:
        binding.variant.status = StatusType.RUNNING.value
        binding.variant.save(update_fields=["status"])
        if not eval_only:
            binding.output_column.status = StatusType.RUNNING.value
            binding.output_column.save(update_fields=["status"])
        terminal = {
            str(item.example_id)
            for item in run.items.filter(
                status__in=["completed", "failed", "skipped"],
                deleted=False,
            )
        }
        pending_rows = [
            link.snapshot_row_id
            for link in links
            if str(link.version_item.example_id) not in terminal
        ]
        if pending_rows and not eval_only:
            _create_running_cells(pending_rows, [binding.output_column_id], dataset_id)
        if pending_rows and run.evaluator_versions:
            from model_hub.views.eval_runner import NativeEvaluationRunner

            selection = run.metadata["native_identity"].get("evaluator_rows", {})
            for pin in run.evaluator_versions:
                eval_rows = [
                    row_id
                    for row_id in pending_rows
                    if not selection
                    or pin["metric_id"] in selection.get(str(row_id), [])
                ]
                evaluators[pin["metric_id"]].prepare_native_rows(eval_rows)
    messages = config["messages"]
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    return {
        "experiment_id": experiment_id,
        "experiment_prompt_config_id": config_id,
        "column_id": str(binding.output_column_id),
        "experiment_dataset_id": str(binding.variant_id),
        "row_ids": rows,
        "messages": messages,
        "model_name": config["model"],
        "model_config": config["configuration"],
        "output_format": config["output_format"],
        "run_prompt_config": {**config["model_config"], **config["model_params"]},
        "status": "READY",
    }


def _settle_cancelled_native_graph(dataset, run, binding, context):
    """Admission locks are held by cleanup. Seal only this plan's unfinished rows.

    No graph/provider/evaluator executes. Existing terminal attempts and Stop
    markers remain untouched; queued/draft admission is not misrepresented as
    having executed. The caller transitions the run to cancelled afterwards.
    """
    import json

    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.services.experiment_submit import _target_for_binding
    from model_hub.services.native_run_recording import record_attempt, start_attempt
    from model_hub.views.eval_runner import NativeEvaluationRunner

    plan = run.metadata["native_identity"]["graph_plan"]
    target = _target_for_binding(binding, run)
    message = "Native graph execution cancelled"
    for row_id in target["snapshot_row_ids"]:
        key = f"native:{binding.execution_id}:{row_id}:1"
        item = start_attempt(
            dataset=dataset, run_id=run.id, snapshot_row_id=row_id, attempt_key=key
        )
        if item.status in {"completed", "failed", "skipped"}:
            continue
        column_ids = set(plan["node_column_mapping"].values())
        refs = []
        for pin in run.evaluator_versions:
            outcomes = []
            for output in plan["terminal_column_ids"]:
                evaluator = NativeEvaluationRunner(
                    dataset=dataset,
                    run=run,
                    binding=binding,
                    pin=pin,
                    output_column_id=output,
                )
                from model_hub.services.experiment_utils import is_user_eval_stopped

                if is_user_eval_stopped(pin["metric_id"]):
                    result = Column.no_workspace_objects.filter(
                        dataset_id=binding.snapshot.snapshot_dataset_id,
                        source=SourceChoices.EXPERIMENT_EVALUATION.value,
                        deleted=False,
                        source_id=f"{binding.variant_id}-{output}-sourceid-{pin['metric_id']}",
                    ).first()
                    reason = None
                else:
                    result = evaluator._native_result_column()
                    reason = evaluator._create_reason_column(
                        binding.snapshot.snapshot_dataset, "", parent_column=result
                    )
                if result is not None:
                    if reason is None:
                        reason = Column.no_workspace_objects.filter(
                            dataset_id=binding.snapshot.snapshot_dataset_id,
                            source=SourceChoices.EVALUATION_REASON.value,
                            deleted=False,
                            source_id=f"{result.id}-sourceid-{pin['metric_id']}",
                        ).first()
                    column_ids.add(str(result.id))
                    if reason is not None:
                        column_ids.add(str(reason.id))
                outcomes.append(
                    {
                        "metric_id": pin["metric_id"],
                        "evaluator_version_id": pin["evaluator_version_id"],
                        "output_column_id": output,
                        "status": "not_assessed",
                        "reason": "execution_cancelled",
                    }
                )
            refs.append(
                outcomes[0]
                if len(outcomes) == 1
                else {
                    "metric_id": pin["metric_id"],
                    "evaluator_version_id": pin["evaluator_version_id"],
                    "status": "not_assessed",
                    "reason": "execution_cancelled",
                    "terminal_results": outcomes,
                }
            )
        defaults = {
            "value": message,
            "value_infos": json.dumps({"reason": message}),
            "status": CellStatus.ERROR.value,
        }
        for column_id in column_ids:
            Cell.no_workspace_objects.get_or_create(
                dataset_id=binding.snapshot.snapshot_dataset_id,
                column_id=column_id,
                row_id=row_id,
                deleted=False,
                defaults=defaults,
            )
            Cell.no_workspace_objects.filter(
                dataset_id=binding.snapshot.snapshot_dataset_id,
                column_id=column_id,
                row_id=row_id,
                deleted=False,
                status=CellStatus.RUNNING.value,
            ).update(**defaults)
        graph = {
            **(item.metadata or {}).get("native_graph", {}),
            "plan_id": context["plan_id"],
            "plan_hash": context["plan_hash"],
            "run_id": str(run.id),
            "example_id": str(item.example_id),
            "trial_number": 1,
            "cancelled": True,
            "sealed": True,
        }
        from django.utils import timezone

        from agent_playground.models import GraphExecution, NodeExecution

        nodes = graph.setdefault("node_results", {})
        for node_id in plan["node_ids"]:
            if nodes.get(node_id, {}).get("status") not in {
                "SUCCESS",
                "FAILED",
                "SKIPPED",
            }:
                nodes[node_id] = {
                    **nodes.get(node_id, {}),
                    "status": "SKIPPED",
                    "outputs": {},
                    "error": message,
                }
        root_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"futureagi:native-graph:{run.id}:{item.example_id}:1"
        )
        expected_input = {
            column["name"]: item.input["values"].get(column["source_id"])
            for column in context["columns"]
        }
        root = GraphExecution.no_workspace_objects.filter(
            pk=root_id,
            graph_version_id=run.agent_version_id,
            parent_node_execution__isnull=True,
            input_payload=expected_input,
            deleted=False,
        ).first()
        graph_ids = {str(root.id)} if root else set()
        children = list(
            GraphExecution.no_workspace_objects.filter(
                pk__in=graph.get("child_results", {}), deleted=False
            ).select_related("parent_node_execution__node")
        )
        # Only stable child allocations descending from the actual matching root
        # are eligible for Stop writes, never arbitrary checkpoint identifiers.
        while children:
            admitted = []
            for child in children:
                parent = child.parent_node_execution
                if (
                    parent is not None
                    and str(parent.graph_execution_id) in graph_ids
                    and child.graph_version_id == parent.node.ref_graph_version_id
                    and str(child.graph_version_id) in plan["definition_hashes"]
                    and child.id
                    == uuid.uuid5(parent.graph_execution_id, str(parent.node_id))
                ):
                    graph_ids.add(str(child.id))
                    admitted.append(child)
            if not admitted:
                break
            children = [child for child in children if child not in admitted]
        graph["graph_execution_allocated"] = root is not None
        for child in graph.get("child_results", {}).values():
            for node in child.get("node_results", {}).values():
                if node.get("status") == "RUNNING":
                    node.update(status="SKIPPED", outputs={}, error=message)
        # Only executions actually allocated by this canonical attempt are fenced.
        NodeExecution.no_workspace_objects.filter(
            graph_execution_id__in=graph_ids,
            deleted=False,
            status__in=["pending", "running"],
        ).update(status="skipped", error_message=message, completed_at=timezone.now())
        GraphExecution.no_workspace_objects.filter(
            pk__in=graph_ids, deleted=False, completed_at__isnull=True
        ).update(status="cancelled", error_message=message, completed_at=timezone.now())
        item.metadata = {**item.metadata, "native_graph": graph}
        item.save(update_fields=["metadata", "updated_at"])
        record_attempt(
            dataset=dataset,
            run_id=run.id,
            snapshot_row_id=row_id,
            attempt_key=key,
            status="skipped",
            error={"type": "ExecutionCancelled", "message": message},
            score_refs=refs,
            trace_id=graph.get("trace_id"),
            root_span_id=graph.get("root_span_id"),
        )


def _cancel_native_runs(experiment_id, contexts, *, include_draft=True):
    from model_hub.services.native_run_recording import mark_native_run
    from model_hub.views.experiment_runner import _native_dispatch

    for context in contexts.values():
        dataset, run, binding = _native_dispatch(experiment_id, context)
        # Failure cleanup cannot turn an unconfirmed admission into a user stop.
        # Explicit stop cleanup retains the ability to cancel draft executions.
        if run.completed_at is None and (include_draft or run.status != "draft"):
            if run.task_kind == "graph" and run.status not in {"draft", "cancelled"}:
                _settle_cancelled_native_graph(dataset, run, binding, context)
            mark_native_run(dataset=dataset, run_id=run.id, status="cancelled")


def _cleanup_native_execution_sync(
    experiment_id, contexts, *, stopped=False, stop_message="Experiment stopped by user"
):
    """Fence every delayed cleanup by admission identity under admission locks."""
    from django.db import transaction

    from model_hub.models.choices import StatusType
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.services.experiment_submit import (
        STATE_KEY,
        _cancel_pending_locked,
        _locked_native_admission,
    )
    from model_hub.views.experiment_runner import _native_dispatch
    from tfc.routers import force_primary

    with force_primary(), transaction.atomic():
        experiment, snapshot, state = _locked_native_admission(experiment_id, contexts)
        no_effect = {
            "experiment_id": experiment_id,
            "cells_cleaned": 0,
            "status": "COMPLETED",
        }
        # Tombstoned and superseded callbacks can never touch current cells/status.
        if state.get("state") == "cancelled" or (snapshot.dataset_config or {}).get(
            STATE_KEY, {}
        ).get("admission_id") != state.get("admission_id"):
            return no_effect
        if state.get("state") == "pending":
            if stopped:
                _cancel_pending_locked(experiment, snapshot, state)
            return no_effect  # Failure is not an explicit user Stop.
        if state.get("state") != "accepted":
            raise RunWriteError("native cleanup has no committed admission")
        for context in contexts.values():
            _native_dispatch(experiment_id, context, full_plan=True)
        _cancel_native_runs(experiment_id, contexts, include_draft=stopped)
        if stopped:
            result = _stop_experiment_cleanup_sync(
                experiment_id, stop_message, manage_connections=False
            )
        else:
            result = _cleanup_running_cells_sync(
                experiment_id, manage_connections=False
            )
            experiment.status = StatusType.FAILED.value
            experiment.save(update_fields=["status"])
        if result["status"] != "COMPLETED":
            raise RunWriteError("native execution cleanup did not complete")
        return result


def _setup_prompt_v2_sync(
    experiment_id: str,
    dataset_id: str,
    experiment_prompt_config_id: str,
    rerun_row_ids: list = None,
    failed_only: bool = False,
    native_context: dict | None = None,
) -> dict:
    """
    V2: Setup a single prompt for processing.

    EDT + EPC already exist (created by V2 view).
    Creates result Column + Cells in RUNNING state.
    Returns all info needed for row processing.
    """
    close_old_connections()

    try:
        if native_context is not None:
            return _setup_native_prompt(
                experiment_id,
                dataset_id,
                experiment_prompt_config_id,
                native_context,
                rerun_row_ids=rerun_row_ids,
                failed_only=failed_only,
            )
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
            "experiment_dataset__experiment__dataset__workspace",
            "prompt_version__original_template__workspace",
        ).get(
            id=experiment_prompt_config_id,
            experiment_dataset__experiment_id=experiment_id,
            experiment_dataset__experiment__snapshot_dataset_id=dataset_id,
        )

        experiment = epc.experiment_dataset.experiment
        experiment_dataset = epc.experiment_dataset
        snapshot_dataset_id = str(experiment.snapshot_dataset_id)

        # Resolve version defaults + explicit request overrides once. Keep the
        # effective settings on this run's EPC so detail/edit and reruns can
        # audit what the worker actually passed to the provider.
        config = resolve_prompt_config(epc)
        if (
            epc.model_params != config["model_params"]
            or epc.configuration != config["configuration"]
        ):
            epc.model_params = config["model_params"]
            epc.configuration = config["configuration"]
            epc.save(update_fields=["model_params", "configuration"])

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


def _setup_native_graph(
    experiment_id, dataset_id, config_id, context, rerun_row_ids=None, failed_only=False
):
    """Idempotent setup owns only the exact graph-row plan, including eval cells."""
    from django.db import transaction

    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.services.experiment_submit import _target_for_binding
    from model_hub.views.eval_runner import NativeEvaluationRunner
    from model_hub.views.experiment_runner import _native_graph_dispatch
    from tfc.routers import force_primary

    with force_primary(), transaction.atomic():
        dataset, run, binding, plan = _native_graph_dispatch(
            experiment_id, dataset_id, config_id, context
        )
        if run.status in {"draft", "cancelled"}:
            raise RunWriteError("Native graph is not admitted or was stopped")
        target = _target_for_binding(binding, run)
        rows = target["snapshot_row_ids"]
        if failed_only or (
            rerun_row_ids and {str(row) for row in rerun_row_ids} != set(rows)
        ):
            raise RunWriteError(
                "Native graph setup cannot change the admitted row selection"
            )
        result = {
            "experiment_id": str(experiment_id),
            "experiment_agent_config_id": str(config_id),
            "experiment_dataset_id": str(binding.variant_id),
            "graph_version_id": str(run.agent_version_id),
            "node_column_mapping": plan["node_column_mapping"],
            "terminal_column_ids": plan["terminal_column_ids"],
            "row_ids": rows,
            "status": "READY",
        }
        if run.completed_at is not None:
            return {
                **result,
                "status": "TERMINAL",
                "terminal_completed_rows": run.items.filter(
                    status="completed", deleted=False
                ).count(),
            }
        from model_hub.services.experiment_utils import is_experiment_cancelled

        if is_experiment_cancelled(run.experiment_id):
            raise RunWriteError("Native graph was stopped before setup")
        binding.variant.status = StatusType.RUNNING.value
        binding.variant.save(update_fields=["status"])
        Column.no_workspace_objects.filter(
            dataset_id=dataset_id,
            pk__in=plan["node_column_mapping"].values(),
            deleted=False,
        ).update(status=StatusType.RUNNING.value)
        # Existing attempts have durable output/evaluator checkpoints. Setup
        # redelivery must not blank them, even before final receipt sealing.
        claimed = {
            str(value)
            for value in run.items.filter(deleted=False).values_list(
                "example_id", flat=True
            )
        }
        pending = [
            row
            for row, example in zip(rows, target["example_ids"], strict=True)
            if example not in claimed
        ]
        if pending:
            _create_running_cells(
                pending, list(plan["node_column_mapping"].values()), dataset_id
            )
            for column in plan["terminal_column_ids"]:
                for pin in run.evaluator_versions:
                    NativeEvaluationRunner(
                        dataset=dataset,
                        run=run,
                        binding=binding,
                        pin=pin,
                        output_column_id=column,
                    ).prepare_native_rows(pending)
        return result


@activity.defn
async def execute_native_graph_activity(input: dict) -> dict:
    """The real graph child executes inside one trace-bearing durable row activity.

    Independent rows remain concurrent. Node/module checkpoints retain DAG
    dependency order across activity retries without duplicating paid effects.
    """
    from model_hub.views.experiment_runner import _run_native_graph_row

    context = input["native_context"]
    async with Heartbeater():
        return await otel_sync_to_async(_run_native_graph_row, thread_sensitive=False)(
            context["experiment_id"],
            context["dataset_id"],
            context["row_id"],
            context["dispatch"],
            graph_execution_id=input["graph_execution_id"],
            graph_version_id=input["graph_version_id"],
            input_payload=input["input_payload"],
        )


def _setup_agent_sync(
    experiment_id: str,
    dataset_id: str,
    experiment_agent_config_id: str,
    rerun_row_ids: list = None,
    failed_only: bool = False,
    native_context: dict | None = None,
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
        from model_hub.services.experiment_target_admission import (
            ensure_agent_worker_admitted,
        )

        # Revalidate the persisted target and all supplied bindings before writes.
        eac = ensure_agent_worker_admitted(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            experiment_agent_config_id=experiment_agent_config_id,
            row_ids=rerun_row_ids,
        )
        experiment = eac.experiment_dataset.experiment
        experiment_dataset = eac.experiment_dataset
        snapshot_dataset_id = str(experiment.snapshot_dataset_id)

        if native_context is not None:
            return _setup_native_graph(
                experiment_id,
                dataset_id,
                experiment_agent_config_id,
                native_context,
                rerun_row_ids=rerun_row_ids,
                failed_only=failed_only,
            )

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
    experiment_agent_config_id: str = "",
    native_context: dict | None = None,
) -> dict:
    """
    Prepare a single row for graph execution.

    Reads base-data cells from the snapshot dataset, builds an input_payload
    mapping column names to values, and creates a GraphExecution record via
    the agent_playground client helper.
    """
    close_old_connections()

    try:
        from django.db.models import Q

        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Cell
        from model_hub.services.experiment_target_admission import (
            ensure_agent_worker_admitted,
        )
        from tfc.temporal.agent_playground.client import _create_graph_execution

        # Admission precedes even reading row payloads, graph creation or budgets.
        eac = ensure_agent_worker_admitted(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            experiment_agent_config_id=experiment_agent_config_id,
            graph_version_id=graph_version_id,
            row_id=row_id,
        )

        if native_context is not None:
            from model_hub.views.experiment_runner import _prepare_native_graph_row

            return _prepare_native_graph_row(
                experiment_id,
                dataset_id,
                experiment_agent_config_id,
                row_id,
                native_context,
            )

        # Experiment-generated column sources to exclude from input
        EXCLUDE_SOURCES = {
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        }

        # Get base-data cells for this row (exclude experiment-generated columns)
        cells = (
            Cell.objects.filter(
                Q(dataset__isnull=True) | Q(dataset_id=dataset_id),
                row_id=row_id,
                row__dataset_id=dataset_id,
                row__deleted=False,
                column__dataset_id=dataset_id,
                column__deleted=False,
                deleted=False,
            )
            .exclude(column__source__in=EXCLUDE_SOURCES)
            .select_related("column")
        )

        input_payload = {cell.column.name: cell.value for cell in cells}

        # Use the validated persisted version, not the incoming identifier.
        graph_execution_id = _create_graph_execution(
            graph_version_id=eac.graph_version_id,
            input_payload=input_payload,
        )
        from model_hub.services.experiment_row_budget import bind_graph_budget

        bind_graph_budget(experiment_id, row_id, str(eac.pk), graph_execution_id)

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
        from model_hub.services.experiment_target_admission import worker_error

        activity.logger.exception(f"Error in _prepare_agent_row_sync: {e}")
        return {
            "row_id": row_id,
            "graph_execution_id": "",
            "input_payload": {},
            "status": "FAILED",
            "error": worker_error(e),
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
    output_format: str | None,
    run_prompt_config: dict | None,
    native_context: dict | None = None,
) -> dict:
    """Synchronous implementation of process_row."""
    close_old_connections()

    try:
        from model_hub.views.experiment_runner import _process_row_impl

        # Call existing implementation (skip Celery evals - Temporal handles them separately)
        result = _process_row_impl(
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
            **(
                {"native_context": native_context} if native_context is not None else {}
            ),
        )
        if native_context is not None:
            return {
                "row_id": row_id,
                "column_id": column_id,
                "status": result["status"],
            }

        # NOTE: Do NOT call check_and_update_column_status here!
        # In Temporal, evaluations run AFTER all rows complete (in the workflow).
        # If we check column status here, the result column will be marked COMPLETED
        # before evaluations finish, causing the experiment to complete prematurely.
        # The workflow handles status checks at the right time (after evals complete).

        from tfc.temporal.experiments.run_policy import (
            persisted_prompt_status,
            reports_failed_rows,
        )

        return {
            "row_id": row_id,
            "column_id": column_id,
            "status": (
                persisted_prompt_status(row_id, column_id, dataset_id, experiment_id)
                if reports_failed_rows()
                else "COMPLETED"
            ),
        }
    except Exception as e:
        if native_context is not None:
            # Admission/provenance/identity refusal must not alter any classic
            # cell through the legacy unscoped error handler below.
            raise
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
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Column, Row
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
        # A base evaluation call already sent when the experiment is stopped
        # finishes, and its result is kept: it has been paid for.
        runner.keep_results_after_stop = True
        # Override dataset to use experiment's dataset for base evaluations.
        # This is critical because user_eval_metric.dataset may differ from
        # experiment.dataset, and we need to process rows from the experiment's dataset.
        runner.dataset = dataset

        # Per-row cancel check — mirrors the Celery path in
        # model_hub/tasks/user_evaluation.py:329-332. If the runner exposes
        # the callback attribute, point it at evaluation_tracker so that
        # StopUserEvalView's request_cancel breaks the loop at row level.
        if hasattr(runner, "_check_cancel_callback"):
            runner._check_cancel_callback = lambda eid=user_eval_metric.id: (
                evaluation_tracker.should_cancel(eid)
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
            # The experiment's Stop cancels the experiment: send no new batch.
            # Rows of the batches already sent keep their results; rows never
            # sent are marked stopped instead of left running.
            if is_experiment_cancelled(uuid.UUID(experiment_id)):
                activity.logger.info(
                    f"Base evaluation {eval_template_id} stopped with the "
                    f"experiment; processed {i}/{total_rows} rows before stop"
                )
                mark_eval_cells_stopped(
                    user_eval_metric,
                    reason="Experiment stopped",
                    dataset_ids=[dataset.id],
                )
                return {
                    "experiment_id": experiment_id,
                    "eval_template_id": eval_template_id,
                    "status": "CANCELLED",
                }

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


def _check_experiment_dataset_status_sync(entity_id: str, native_context=None) -> dict:
    """Synchronous implementation of check_experiment_dataset_status."""
    close_old_connections()

    try:
        from model_hub.models.experiments import ExperimentDatasetTable
        from model_hub.views.experiment_runner import (
            check_and_update_experiment_dataset_status,
        )

        if native_context is not None:
            from model_hub.services.experiment_run_writes import RunWriteError
            from model_hub.services.native_run_recording import finish_native_run
            from model_hub.views.experiment_runner import _native_dispatch

            edt = ExperimentDatasetTable.no_workspace_objects.get(
                id=entity_id, deleted=False
            )
            dataset, run, binding = _native_dispatch(edt.experiment_id, native_context)
            if str(binding.variant_id) != str(entity_id):
                raise RunWriteError("native completion does not match the variant")
            run = finish_native_run(dataset=dataset, run_id=run.id)
            if run.evaluator_versions:
                from model_hub.models.choices import SourceChoices
                from model_hub.models.develop_dataset import Column
                from model_hub.views.eval_runner import NativeEvaluationRunner

                terminals = (
                    run.metadata["native_identity"]["graph_plan"]["terminal_column_ids"]
                    if run.task_kind == "graph"
                    else [str(binding.output_column_id)]
                )
                for output in terminals:
                    for pin in run.evaluator_versions:
                        evaluator = NativeEvaluationRunner(
                            dataset=dataset,
                            run=run,
                            binding=binding,
                            pin=pin,
                            **(
                                {"output_column_id": output}
                                if run.task_kind == "graph"
                                else {}
                            ),
                        )
                        column = Column.no_workspace_objects.filter(
                            dataset=binding.snapshot.snapshot_dataset,
                            deleted=False,
                            source=SourceChoices.EXPERIMENT_EVALUATION.value,
                            source_id=f"{binding.variant_id}-{output}-sourceid-{pin['metric_id']}",
                        ).first()
                        if column is not None:
                            evaluator._check_and_update_eval_status(column.id)
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
        if input.admission_only:
            from model_hub.services.experiment_submit import (
                SubmissionCancelled,
                native_admission_ready,
            )

            try:
                if input.native_runs:
                    ready = await otel_sync_to_async(
                        native_admission_ready, thread_sensitive=False
                    )(
                        input.experiment_id,
                        input.native_runs,
                    )
                else:
                    from model_hub.services.http_experiment_source import (
                        http_admission_ready,
                    )

                    ready = await otel_sync_to_async(
                        http_admission_ready, thread_sensitive=False
                    )(
                        input.experiment_id,
                        input.http_admission_id,
                    )
            except SubmissionCancelled:
                return SetupExperimentOutput(
                    experiment_id=input.experiment_id,
                    dataset_id="",
                    prompt_configs=[],
                    eval_template_ids=[],
                    agent_configs=[],
                    has_base_column=False,
                    status="ADMISSION_CANCELLED",
                )
            return SetupExperimentOutput(
                experiment_id=input.experiment_id,
                dataset_id="",
                prompt_configs=[],
                eval_template_ids=[],
                agent_configs=[],
                has_base_column=False,
                status="READY" if ready else "ADMISSION_PENDING",
            )
        async with Heartbeater():
            result = await otel_sync_to_async(
                _setup_experiment_sync, thread_sensitive=False
            )(input.experiment_id, input.http_admission_id)

        return SetupExperimentOutput(
            experiment_id=result["experiment_id"],
            dataset_id=result["dataset_id"],
            prompt_configs=result["prompt_configs"],
            eval_template_ids=result["eval_template_ids"],
            status=result["status"],
            agent_configs=result.get("agent_configs", []),
            http_sources=result.get("http_sources", []),
            experiment_type=result.get("experiment_type", "llm"),
            has_base_column=result.get("has_base_column", True),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error setting up experiment {input.experiment_id}: {e}"
        )

        # A read-only acknowledgement poll must not mutate pending intent.
        if not input.admission_only:
            try:
                if input.http_admission_id:
                    await otel_sync_to_async(
                        _http_execution_control_sync, thread_sensitive=False
                    )(
                        input.experiment_id,
                        input.http_admission_id,
                        "fail",
                    )
                else:
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
                native_context=input.native_context,
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
        if input.native_context is not None:
            # The parent waits for acknowledgement before child setup. Any
            # remaining identity/configuration failure must still fail closed.
            raise RuntimeError("Native prompt setup is not available") from e
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
                native_context=input.native_context,
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
            terminal_completed_rows=result.get("terminal_completed_rows", 0),
        )

    except Exception as e:
        if input.native_context is not None:
            raise
        from model_hub.services.experiment_target_admission import worker_error

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
            error=worker_error(e),
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
            input.experiment_agent_config_id,
            native_context=input.native_context,
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
                **(
                    {"native_context": input.native_context}
                    if input.native_context is not None
                    else {}
                ),
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
        )(input.entity_id, native_context=input.native_context)

        return CheckStatusOutput(
            entity_id=result["entity_id"],
            is_complete=result["is_complete"],
            status=result["status"],
        )

    except Exception as e:
        if input.native_context is not None:
            raise
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
            http_sources=result.get("http_sources", []),
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
        if input.native_runs:
            result = await otel_sync_to_async(
                _cleanup_native_execution_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.native_runs,
                stopped=False,
            )
        elif input.http_admission_id:
            result = await otel_sync_to_async(
                _http_execution_control_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.http_admission_id,
                "cleanup",
            )
        else:
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
        if input.native_runs:
            raise
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
    http_context: dict | None = None,
    rows_per_batch: int | None = None,
) -> dict:
    """
    Process a batch of rows for a single eval template.
    Processes all rows in the batch for the specified template. With
    ``rows_per_batch`` (rerun evaluations, which pass every row), the rows go
    in batches and no new batch is sent once the experiment is cancelled.
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
        from model_hub.services.http_evaluation_fence import validate_http_eval_input
        from model_hub.views.eval_runner import EvaluationRunner

        validate_http_eval_input(
            http_context,
            experiment_id,
            dataset_id,
            column_id,
            row_ids,
            [eval_template_id],
        )

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
        if (experiment_dataset.legacy_prompt_config or {}).get(
            "target_type"
        ) == "http" and http_context is None:
            raise ValueError("HTTP evaluation requires its admission identity.")
        eval_template = UserEvalMetric.objects.select_related("template").get(
            id=uuid.UUID(eval_template_id)
        )

        config = {
            "dataset_id": str(experiment_dataset.id),
            "input": str(column.id),
            "experiment_id": str(experiment.id),
            "source": "experiment",
        }
        if http_context is not None:
            config["http_context"] = http_context

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

        if not rows_per_batch:
            # Run evaluation for the batch of rows
            runner.run_prompt(row_ids=row_ids)
        else:
            from model_hub.utils.eval_cell_status import mark_eval_cells_stopped

            # A call already sent when the experiment is stopped finishes,
            # and its result is kept: it has been paid for.
            runner.keep_results_after_stop = True
            for start in range(0, len(row_ids), rows_per_batch):
                # The experiment's Stop cancels the experiment: send no new
                # batch; rows never sent are marked stopped, in this
                # experiment's dataset only.
                if is_experiment_cancelled(uuid.UUID(experiment_id)):
                    activity.logger.info(
                        f"Batch eval {eval_template_id} stopped with the "
                        f"experiment; sent {start}/{len(row_ids)} rows"
                    )
                    mark_eval_cells_stopped(
                        eval_template,
                        reason="Experiment stopped",
                        dataset_ids=[experiment.snapshot_dataset_id],
                    )
                    return {
                        "row_ids": row_ids,
                        "column_id": column_id,
                        "eval_template_id": eval_template_id,
                        "status": "CANCELLED",
                    }
                runner.run_prompt(row_ids=row_ids[start : start + rows_per_batch])

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
                http_context=input.http_context,
                rows_per_batch=input.rows_per_batch,
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
    http_context: dict | None = None,
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
        from model_hub.services.http_evaluation_fence import (
            http_evaluation_write,
            validate_http_eval_input,
        )

        validate_http_eval_input(
            http_context,
            experiment_id,
            dataset_id,
            column_id,
            failed_row_ids,
            eval_template_ids,
        )
        with http_evaluation_write(http_context):
            import json

            from model_hub.models.choices import CellStatus, SourceChoices, StatusType
            from model_hub.models.develop_dataset import Column, Row
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

            experiment = ExperimentsTable.objects.select_related(
                "snapshot_dataset"
            ).get(id=uuid.UUID(experiment_id), deleted=False)
            experiment_dataset = ExperimentDatasetTable.objects.get(id=column.source_id)
            if (experiment_dataset.legacy_prompt_config or {}).get(
                "target_type"
            ) == "http" and http_context is None:
                raise ValueError("HTTP evaluation requires its admission identity.")
            dataset = experiment.snapshot_dataset
            from model_hub.utils.snapshot_scope import same_workspace_scope
            from tfc.temporal.experiments.run_policy import failed_row_values

            policy_values = failed_row_values(
                experiment.run_policy.get("error_policy"), error_message
            )
            # A source with no workspace is in its organization's default
            # workspace, where older snapshots of it were saved.
            if policy_values and (
                str(dataset.id) != str(dataset_id)
                or column.dataset_id != dataset.id
                or experiment_dataset.experiment_id != experiment.id
                or dataset.organization_id != experiment.dataset.organization_id
                or not same_workspace_scope(
                    dataset.workspace_id,
                    experiment.dataset.workspace_id,
                    experiment.dataset.organization_id,
                )
            ):
                raise ValueError(
                    "Error policy target does not match the experiment snapshot."
                )

            if policy_values:
                from django.db.models import Q

                workspace_scope = Q(workspace_id=experiment.dataset.workspace_id)
                if (
                    not experiment.dataset.workspace_id
                    or experiment.dataset.workspace.is_default
                ):
                    workspace_scope |= Q(workspace__isnull=True) | Q(
                        workspace__is_default=True
                    )
                admitted_rows = set(
                    Row.objects.filter(
                        id__in=failed_row_ids, dataset=dataset, deleted=False
                    ).values_list("id", flat=True)
                )
                admitted_evals = set(
                    experiment.user_eval_template_ids.filter(
                        workspace_scope,
                        id__in=eval_template_ids,
                        deleted=False,
                        organization_id=experiment.dataset.organization_id,
                    ).values_list("id", flat=True)
                )
                if admitted_rows != {
                    uuid.UUID(str(r)) for r in failed_row_ids
                } or admitted_evals != {uuid.UUID(str(e)) for e in eval_template_ids}:
                    raise ValueError(
                        "Error policy rows or evaluations are outside the experiment."
                    )

            error_value = json.dumps({"error": error_message})
            from django.db import transaction

            from model_hub.services.experiment_utils import (
                is_experiment_cancelled,
                is_user_eval_stopped,
            )
            from tfc.temporal.experiments.run_policy import (
                evaluation_stopped,
                write_failed_row_cells,
            )

            # Bulk fetch the failed rows once for every template
            row_uuids = [uuid.UUID(rid) for rid in failed_row_ids]
            rows = Row.objects.filter(id__in=row_uuids, dataset=dataset, deleted=False)
            row_map = {str(r.id): r for r in rows}
            missing = set(failed_row_ids) - set(row_map.keys())
            for rid in missing:
                activity.logger.warning(f"Row {rid} not found in dataset {dataset.id}")

            score_values = policy_values or {
                "value": error_value,
                "status": CellStatus.ERROR.value,
                "value_infos": json.dumps({"reason": error_message}),
            }
            reason_values = {
                "value": error_message,
                "status": CellStatus.ERROR.value,
                "value_infos": json.dumps({"reason": error_message}),
            }

            for eval_template_id in eval_template_ids:
                try:
                    # A whole-experiment stop owns every cell it has not settled.
                    if is_experiment_cancelled(experiment.id):
                        return {
                            "cells_created": cells_created,
                            "status": "SKIPPED",
                        }

                    eval_template = UserEvalMetric.objects.select_related(
                        "template"
                    ).get(id=uuid.UUID(eval_template_id))

                    config = {
                        "dataset_id": str(experiment_dataset.id),
                        "input": str(column.id),
                        "experiment_id": str(experiment.id),
                        "source": "experiment",
                    }

                    runner = None
                    if eval_template.template.template_type == "composite":
                        # Composite parents have no single evaluator class: build
                        # their result column the way experiment setup does.
                        from model_hub.tasks.composite_runner import (
                            CompositeEvaluationRunner,
                        )
                        from model_hub.views.experiment_runner import (
                            _get_or_create_eval_column,
                        )

                        eval_column = _get_or_create_eval_column(
                            dataset,
                            CompositeEvaluationRunner.build_column_config(
                                user_eval_metric=eval_template,
                                dataset=dataset,
                                experiment_dataset=experiment_dataset,
                                column=column,
                            ),
                            experiment_dataset=experiment_dataset,
                        )
                    else:
                        # format_output skips the runner's own start-up, which
                        # would mark the evaluation RUNNING (and so un-stop it).
                        runner = EvaluationRunner(
                            user_eval_metric_id=eval_template.id,
                            experiment_dataset=experiment_dataset,
                            is_only_eval=False,
                            source="experiment",
                            source_id=str(eval_template.template.id),
                            source_configs=config,
                            column=column,
                            format_output=True,
                        )
                        runner.dataset = dataset
                        runner.load_user_eval_metric()

                        column_config = runner._get_column_config(dataset)
                        eval_column = runner._create_or_update_column(
                            dataset, column_config, new_column=True
                        )

                    # Stop guard: skip writing error cells if the user already
                    # stopped this eval — otherwise the late error path
                    # overwrites the "User stopped evaluation" marker.
                    if is_user_eval_stopped(eval_template.id):
                        continue

                    # Handle the reason column: create it if enabled, and settle an
                    # existing one either way (setup creates and resets it).
                    reason_column = None
                    if runner is not None and eval_template.config.get("reason_column"):
                        reason_column_name = (
                            f"{eval_template.name}-{column.name}-reason"
                        )
                        reason_column = runner._create_reason_column(
                            dataset, reason_column_name, parent_column=eval_column
                        )
                    elif runner is not None:
                        reason_column = Column.objects.filter(
                            dataset=dataset,
                            source=SourceChoices.EVALUATION_REASON.value,
                            source_id=f"{eval_column.id}-sourceid-{eval_template.id}",
                            deleted=False,
                        ).first()

                    # The stop cleanup cancels the experiment before it marks any
                    # cell; holding the experiment row while writing means a stop
                    # either sees these results or this write sees the stop.
                    with transaction.atomic():
                        locked = (
                            ExperimentsTable.objects.select_for_update()
                            .filter(id=experiment.id, deleted=False)
                            .values_list("status", flat=True)
                            .first()
                        )
                        if locked in (None, StatusType.CANCELLED.value):
                            return {
                                "cells_created": cells_created,
                                "status": "SKIPPED",
                            }
                        written = write_failed_row_cells(
                            dataset, eval_column, row_map.values(), score_values
                        )
                        if reason_column is not None:
                            write_failed_row_cells(
                                dataset, reason_column, row_map.values(), reason_values
                            )
                        # A per-evaluation stop saves its status before it marks
                        # cells: if it landed, undo these writes and keep its marks.
                        if evaluation_stopped(eval_template.id):
                            transaction.set_rollback(True)
                            written = 0
                    cells_created += written

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
                http_context=input.http_context,
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
    experiment_agent_config_id: str = "",
    native_context: dict | None = None,
) -> dict:
    """Create agent output cells in ERROR state when graph-level failure occurs.

    Unlike eval cells, agent columns already exist. We just need to write
    error cells for any columns that didn't get written by node sinks.
    """
    close_old_connections()
    cells_created = 0

    try:
        import json

        from model_hub.models.choices import CellStatus, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.experiments import ExperimentsTable
        from model_hub.services.experiment_run_writes import RunWriteError
        from model_hub.services.experiment_utils import is_experiment_cancelled
        from model_hub.utils.snapshot_scope import same_workspace_scope
        from tfc.temporal.experiments.run_policy import experiment_write_lock

        # Resolve actual ownership before any write, including for legacy
        # direct callers that do not carry a config or canonical context.
        # A source with no workspace is in its organization's default
        # workspace, where older snapshots of it were saved.
        experiment = ExperimentsTable.no_workspace_objects.select_related(
            "dataset", "snapshot_dataset"
        ).get(pk=experiment_id, deleted=False)
        if (
            str(experiment.snapshot_dataset_id) != str(dataset_id)
            or experiment.dataset.deleted
            or experiment.snapshot_dataset.deleted
            or experiment.dataset.organization_id
            != experiment.snapshot_dataset.organization_id
            or not same_workspace_scope(
                experiment.snapshot_dataset.workspace_id,
                experiment.dataset.workspace_id,
                experiment.dataset.organization_id,
            )
            or not Row.no_workspace_objects.filter(
                pk=row_id, dataset_id=dataset_id, deleted=False
            ).exists()
        ):
            raise RunWriteError("agent settlement snapshot or row is unavailable")
        columns = list(
            Column.no_workspace_objects.filter(
                pk__in=node_column_mapping.values(),
                dataset_id=dataset_id,
                deleted=False,
                source=SourceChoices.EXPERIMENT.value,
                experiments_dataset_column__experiment=experiment,
                experiments_dataset_column__deleted=False,
            ).distinct()
        )
        if len(columns) != len(node_column_mapping) or any(
            str((column.metadata or {}).get("node_id")) not in node_column_mapping
            or node_column_mapping[str((column.metadata or {}).get("node_id"))]
            != str(column.id)
            for column in columns
        ):
            raise RunWriteError("agent settlement columns are outside this experiment")
        if experiment_agent_config_id:
            from model_hub.models.experiments import ExperimentAgentConfig

            native = ExperimentAgentConfig.no_workspace_objects.get(
                pk=experiment_agent_config_id,
                experiment_dataset__experiment=experiment,
                experiment_dataset__deleted=False,
                deleted=False,
            )
            if any(
                str(column.source_id) != str(native.experiment_dataset_id)
                for column in columns
            ):
                raise RunWriteError("agent settlement columns are outside this variant")
        if native_context is not None:
            from model_hub.views.experiment_runner import _native_graph_dispatch

            _, run, _, graph_plan = _native_graph_dispatch(
                experiment_id,
                dataset_id,
                experiment_agent_config_id,
                native_context,
                row_id=row_id,
            )
            if graph_plan["node_column_mapping"] != node_column_mapping:
                raise RunWriteError(
                    "agent settlement columns do not match the immutable plan"
                )
            from model_hub.views.experiment_runner import _run_native_graph_row

            result = _run_native_graph_row(
                experiment_id, dataset_id, row_id, native_context, refusal=True
            )
            return {
                "cells_created": 0,
                "status": "SUCCESS",
                "row_status": "COMPLETED"
                if result["status"] == "SUCCESS"
                else "FAILED",
            }
        with experiment_write_lock(experiment_id):
            if is_experiment_cancelled(experiment_id):
                return {"cells_created": 0, "status": "SUCCESS"}
            for column in columns:
                # A conditional update cannot overwrite terminal/Stop markers
                # even if a node sink finishes between lookup and settlement.
                # Setup owns creation: missing cells are persistence failures.
                cells_created += Cell.no_workspace_objects.filter(
                    column=column,
                    row_id=row_id,
                    dataset_id=dataset_id,
                    deleted=False,
                    status=CellStatus.RUNNING.value,
                ).update(
                    value=error_message,
                    value_infos=json.dumps({"reason": error_message}),
                    status=CellStatus.ERROR.value,
                )
                if not Cell.no_workspace_objects.filter(
                    column=column, row_id=row_id, dataset_id=dataset_id, deleted=False
                ).exists():
                    raise RunWriteError(
                        "agent output cell disappeared before settlement"
                    )

        return {"cells_created": cells_created, "status": "SUCCESS"}
    except Exception as e:
        if native_context is not None:
            raise
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
            experiment_agent_config_id=input.experiment_agent_config_id,
            native_context=input.native_context,
        )

        return CreateErrorAgentCellsOutput(
            cells_created=result["cells_created"],
            status=result["status"],
            error=result.get("error"),
            row_status=result.get("row_status"),
        )

    except Exception as e:
        if input.native_context is not None:
            raise
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
                    runner._check_cancel_callback = lambda eid=eval_template.id: (
                        evaluation_tracker.should_cancel(eid)
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


def _stop_experiment_cleanup_sync(
    experiment_id: str, stop_message: str, *, manage_connections=True
) -> dict:
    """
    Cleanup DB state when an experiment is stopped by user.

    Marks all RUNNING cells as ERROR with the stop message,
    RUNNING columns and EDTs as FAILED, and experiment as CANCELLED.
    """
    if manage_connections:
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

        # Cancel first: late cell writers re-check this status under the
        # experiment row lock, so no write can land after a cell is marked.
        ExperimentsTable.objects.filter(id=experiment.id).update(
            status=StatusType.CANCELLED.value
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
        if manage_connections:
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
        if input.native_runs:
            result = await otel_sync_to_async(
                _cleanup_native_execution_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.native_runs,
                stopped=True,
                stop_message=input.stop_message,
            )
        elif input.http_admission_id:
            result = await otel_sync_to_async(
                _http_execution_control_sync, thread_sensitive=False
            )(
                input.experiment_id,
                input.http_admission_id,
                "stop",
                input.stop_message,
            )
        else:
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
        if input.native_runs:
            raise
        activity.logger.exception(
            f"Error in stop_experiment_cleanup_activity for {input.experiment_id}: {e}"
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
    "execute_native_graph_activity",
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


def _http_execution_control_sync(
    experiment_id, admission_id, action, stop_message="Execution was stopped by user"
):
    """Fence delayed setup/failure/Stop callbacks by the committed admission."""
    from django.db import transaction

    from model_hub.models.choices import StatusType
    from model_hub.models.experiments import ExperimentsTable
    from model_hub.services.experiment_submit import (
        _cancel_pending_locked,
        _locked_native_admission,
    )
    from model_hub.services.http_experiment_source import http_source_ids
    from tfc.routers import force_primary

    close_old_connections()
    try:
        with force_primary(), transaction.atomic():
            experiment, snapshot, state = _locked_native_admission(experiment_id)
            no_effect = {
                "experiment_id": experiment_id,
                "cells_cleaned": 0,
                "status": "COMPLETED",
            }
            if http_source_ids(experiment):
                if (
                    state.get("admission_id") != admission_id
                    or state.get("state") == "cancelled"
                ):
                    return no_effect
                if state.get("state") == "pending":
                    if action == "stop":
                        cancelled = _cancel_pending_locked(experiment, snapshot, state)
                        if cancelled is None:
                            raise ValueError(
                                "Pending HTTP admission was not cancelled."
                            )
                    return no_effect
            if state.get("http_plan") and action in {"stop", "cleanup"}:
                from model_hub.services.http_experiment_source import (
                    cleanup_http_mutation,
                )

                return cleanup_http_mutation(
                    experiment, state, stopped=action == "stop"
                )
            if action == "stop":
                return _stop_experiment_cleanup_sync(
                    experiment_id, stop_message, manage_connections=False
                )
            if action == "fail":
                ExperimentsTable.no_workspace_objects.filter(pk=experiment_id).exclude(
                    status=StatusType.CANCELLED.value,
                ).update(status=StatusType.FAILED.value)
                return no_effect
            return _cleanup_running_cells_sync(experiment_id, manage_connections=False)
    finally:
        close_old_connections()


@activity.defn
async def mark_http_experiment_failed_activity(input: HttpSourceInput) -> None:
    await otel_sync_to_async(_http_execution_control_sync, thread_sensitive=False)(
        input.experiment_id,
        input.admission_id,
        "fail",
    )


async def _http_sync(function, *args):
    def call():
        close_old_connections()
        try:
            return function(*args)
        finally:
            close_old_connections()

    return await otel_sync_to_async(call, thread_sensitive=False)()


@activity.defn
async def setup_http_source_activity(input: HttpSourceInput) -> dict:
    from model_hub.services.http_experiment_source import setup_http_source

    return await _http_sync(
        setup_http_source, input.experiment_id, input.source_id, input.admission_id
    )


@activity.defn
async def process_http_row_activity(input: HttpRowInput) -> dict:
    from model_hub.views.experiment_runner import ExperimentRunner

    def run():
        return ExperimentRunner(uuid.UUID(input.experiment_id)).process_http_row(
            source_id=input.source_id,
            row_id=input.row_id,
            admission_id=input.admission_id,
        )

    return await _http_sync(run)


@activity.defn
async def settle_http_row_activity(input: HttpRowInput) -> dict:
    from model_hub.services.http_experiment_source import settle_http_row

    return await _http_sync(
        settle_http_row,
        input.experiment_id,
        input.source_id,
        input.row_id,
        input.admission_id,
    )


@activity.defn
async def finish_http_source_activity(input: HttpSourceInput) -> dict:
    from model_hub.services.http_experiment_source import finish_http_source

    return await _http_sync(
        finish_http_source, input.experiment_id, input.source_id, input.admission_id
    )
