"""
Temporal workflows for experiment runner.

These workflows orchestrate the experiment execution process.

V1 flow (original Celery-compatible):
1. Setup experiment (load, set RUNNING, prepare eval columns)
2. Process prompts AND run base evaluations IN PARALLEL
3. Wait for all to complete
4. Check and update final experiment status

V2 flow (optimized dependency-aware eval execution):
1. Setup experiment (load EPC/EAC records, set RUNNING)
2. Analyze eval dependencies → split into independent vs dependent
3. Start in parallel:
   a. Independent evals (don't reference 'output')
   b. ProcessPromptV2Workflow per EPC — runs dependent evals per-row immediately
   c. ProcessAgentWorkflow per EAC — same pattern
4. Wait for all to complete
5. Check final experiment status

Note: Activity results are deserialized as dicts by Temporal, so we access
results using dict keys (e.g., result["status"]) rather than attributes.

IMPORTANT: Do NOT use workflow.logger in workflows - it uses Python's stdlib
logging which acquires locks and causes deadlocks. Logging should be done
in activities instead.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import types from separate module (no Django imports, safe for sandbox)
with workflow.unsafe.imports_passed_through():
    from tfc.temporal.agent_playground.types import ExecuteGraphInput as GraphInput
    from tfc.temporal.agent_playground.types import (
        OutputSinkConfig,
    )
    from tfc.temporal.experiments.run_control import ControlledRun
    from tfc.temporal.experiments.types import (  # V2 types; Rerun cells types; Stop experiment types; Error eval cells types; Error agent cells types
        HttpSourceInput,
        HttpRowInput,
        HttpSourceWorkflowInput,
        AnalyzeDependenciesInput,
        CheckStatusInput,
        CleanupRunningCellsInput,
        CreateErrorAgentCellsInput,
        CreateErrorEvalCellsInput,
        GetEvalTemplatesInput,
        MarkExperimentRunningInput,
        PrepareAgentRowInput,
        ProcessAgentWorkflowInput,
        ProcessAgentWorkflowOutput,
        ProcessBatchEvalInput,
        ProcessPromptInput,
        ProcessPromptV2WorkflowInput,
        ProcessPromptV2WorkflowOutput,
        ProcessPromptWorkflowInput,
        ProcessPromptWorkflowOutput,
        ProcessRowInput,
        RerunCellsV2WorkflowInput,
        RerunCellsV2WorkflowOutput,
        ResolveEdtColumnsInput,
        RunEvaluationInput,
        RunExperimentInput,
        RunExperimentOutput,
        SetupAgentInput,
        SetupExperimentInput,
        SetupPromptV2Input,
        StopExperimentCleanupInput,
    )


# =============================================================================
# Retry Policies
# =============================================================================

# Retry policy for setup activities (fast, few retries)
SETUP_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# Retry policy for row processing (longer, more retries for LLM calls)
ROW_PROCESSING_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
    non_retryable_error_types=["ValueError"],  # Don't retry validation errors
)

# Retry policy for status checks (quick retries)
STATUS_CHECK_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=5,
    backoff_coefficient=1.5,
)


# =============================================================================
# Helper functions
# =============================================================================


def get_result_field(result, field: str, default=None):
    """Get a field from an activity result, handling both dict and dataclass."""
    if isinstance(result, dict):
        return result.get(field, default)
    return getattr(result, field, default)


def get_model_name(model: Any) -> str:
    """Extract model name from model specification (can be str or dict)."""
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        return model.get("name", str(model))
    return str(model)


# =============================================================================
# Workflows
# =============================================================================


@workflow.defn
class ProcessPromptWorkflow:
    """
    Child workflow to process a single prompt configuration.

    Handles:
    - Creating column and cells via process_prompt_activity
    - Processing all rows with controlled concurrency
    - Reporting completion status
    """

    @workflow.run
    async def run(
        self, input: ProcessPromptWorkflowInput
    ) -> ProcessPromptWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks
        model_name = get_model_name(input.model)

        # Step 1: Setup column and get row IDs
        prompt_result = await workflow.execute_activity(
            "process_prompt_activity",
            ProcessPromptInput(
                experiment_id=input.experiment_id,
                dataset_id=input.dataset_id,
                prompt_config=input.prompt_config,
                model=input.model,  # Pass as-is (can be str or dict)
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

        # Access result as dict (Temporal deserializes as dict)
        if get_result_field(prompt_result, "status") == "FAILED":
            return ProcessPromptWorkflowOutput(
                experiment_id=input.experiment_id,
                column_id=get_result_field(prompt_result, "column_id", ""),
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="FAILED",
            )

        column_id = get_result_field(prompt_result, "column_id")
        experiment_dataset_id = get_result_field(prompt_result, "experiment_dataset_id")
        row_ids = get_result_field(prompt_result, "row_ids", [])
        total_rows = len(row_ids)

        # Get processing parameters from activity result
        messages = get_result_field(prompt_result, "messages", [])
        actual_model_name = get_result_field(prompt_result, "model_name", model_name)
        model_config = get_result_field(prompt_result, "model_config", {})
        output_format = get_result_field(prompt_result, "output_format")
        run_prompt_config = get_result_field(prompt_result, "run_prompt_config")

        if total_rows == 0:
            return ProcessPromptWorkflowOutput(
                experiment_id=input.experiment_id,
                column_id=column_id,
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="COMPLETED",
            )

        # Step 2a: Process all rows first (without evals)
        # Process in batches to avoid deadlock from creating too many coroutines at once
        completed_count = 0
        failed_count = 0
        completed_row_ids = []

        batch_size = input.max_concurrent_rows  # Process this many rows concurrently

        for batch_start in range(0, total_rows, batch_size):
            batch_end = min(batch_start + batch_size, total_rows)
            batch_row_ids = row_ids[batch_start:batch_end]
            # Create activity tasks for this batch only
            batch_tasks = []
            for row_id in batch_row_ids:
                batch_tasks.append(
                    workflow.execute_activity(
                        "process_row_activity",
                        ProcessRowInput(
                            row_id=row_id,
                            column_id=column_id,
                            dataset_id=input.dataset_id,
                            experiment_id=input.experiment_id,
                            messages=messages,
                            model=actual_model_name,
                            model_config=model_config,
                            output_format=output_format,
                            run_prompt_config=run_prompt_config,
                        ),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=ROW_PROCESSING_RETRY_POLICY,
                    )
                )

            # Process batch concurrently
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            # Collect results from this batch
            for row_id, result in zip(batch_row_ids, batch_results, strict=False):
                if isinstance(result, Exception):
                    failed_count += 1
                else:
                    status = get_result_field(result, "status")
                    if status == "COMPLETED":
                        completed_count += 1
                        completed_row_ids.append(row_id)
                    else:
                        failed_count += 1

        # NOTE: Do NOT check column status here!
        # Evaluations still need to run. If we check column status now,
        # the result column will be marked COMPLETED (all cells are PASS/ERROR),
        # which cascades to mark experiment_dataset and experiment as COMPLETED
        # BEFORE evaluations finish.

        # Step 2b: Get eval template IDs
        eval_template_ids = []
        if completed_row_ids:
            eval_templates_result = await workflow.execute_activity(
                "get_eval_templates_activity",
                GetEvalTemplatesInput(
                    column_id=column_id,
                    experiment_id=input.experiment_id,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )
            eval_template_ids = get_result_field(
                eval_templates_result, "eval_template_ids", []
            )

        # Step 2c: Process evals in batches of 10 rows
        if completed_row_ids and eval_template_ids:
            batch_size = 10
            for i in range(0, len(completed_row_ids), batch_size):
                batch_row_ids = completed_row_ids[i : i + batch_size]
                # Create activity tasks for each template (all process same batch)
                template_tasks = []
                for eval_template_id in eval_template_ids:
                    template_tasks.append(
                        workflow.execute_activity(
                            "process_batch_template_eval_activity",
                            ProcessBatchEvalInput(
                                row_ids=batch_row_ids,
                                column_id=column_id,
                                dataset_id=input.dataset_id,
                                experiment_id=input.experiment_id,
                                eval_template_id=eval_template_id,
                            ),
                            start_to_close_timeout=timedelta(hours=12),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=ROW_PROCESSING_RETRY_POLICY,
                        )
                    )

                # Process all templates for this batch in parallel
                await asyncio.gather(*template_tasks, return_exceptions=True)

        # Step 2d: Update column and experiment_dataset status AFTER all evals complete
        # This must happen outside the eval_template_ids block to handle both cases:
        # - With evals: status is checked after all eval batches finish
        # - Without evals: status is checked after all rows finish
        await workflow.execute_activity(
            "check_column_status_activity",
            CheckStatusInput(entity_type="column", entity_id=column_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=STATUS_CHECK_RETRY_POLICY,
        )

        # Update experiment_dataset status to trigger cascade to experiment
        if experiment_dataset_id:
            await workflow.execute_activity(
                "check_experiment_dataset_status_activity",
                CheckStatusInput(
                    entity_type="experiment_dataset",
                    entity_id=experiment_dataset_id,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

        return ProcessPromptWorkflowOutput(
            experiment_id=input.experiment_id,
            column_id=column_id,
            total_rows=total_rows,
            completed_rows=completed_count,
            failed_rows=failed_count,
            status="COMPLETED" if failed_count == 0 else "PARTIAL",
        )


@workflow.defn
class RunExperimentWorkflow:
    """
    Main workflow to run an experiment.

    Orchestrates (matching original Celery flow):
    1. Setup experiment (load, set status to RUNNING, prepare eval columns)
    2. Process each prompt_config x model combination (child workflows) IN PARALLEL with
    3. Run base column evaluations IN PARALLEL
    4. Wait for all to complete
    5. Check and update final experiment status
    """

    @workflow.run
    async def run(self, input: RunExperimentInput) -> RunExperimentOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        total_rows_processed = 0
        total_failed_rows = 0

        try:
            # Step 0: Cleanup any stale running cells from previous terminated/failed runs
            # This ensures a clean slate before starting the experiment
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(experiment_id=input.experiment_id),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass  # Continue even if cleanup fails

            # Step 1: Setup experiment
            setup_result = await workflow.execute_activity(
                "setup_experiment_activity",
                SetupExperimentInput(experiment_id=input.experiment_id),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=SETUP_RETRY_POLICY,
            )

            # Access result as dict (Temporal deserializes as dict)
            if get_result_field(setup_result, "status") != "READY":
                raise Exception(
                    f"Failed to setup experiment: {get_result_field(setup_result, 'status')}"
                )

            prompt_configs = get_result_field(setup_result, "prompt_configs", [])
            eval_template_ids = get_result_field(setup_result, "eval_template_ids", [])
            dataset_id = get_result_field(setup_result, "dataset_id")

            # Step 2 & 3: Launch prompt processing AND base evaluations IN PARALLEL
            # This matches the original Celery behavior
            _all_tasks = []

            # Step 2: Start child workflows for each prompt_config x model combination
            child_workflow_handles = []
            for prompt_idx, prompt_config in enumerate(prompt_configs):
                _prompt_name = prompt_config.get("name", f"prompt-{prompt_idx}")
                models = prompt_config.get("model", [])

                for model_idx, model in enumerate(models):
                    model_name = get_model_name(model)

                    # Sanitize model name for workflow ID (remove special chars)
                    safe_model_name = "".join(
                        c if c.isalnum() or c in "-_" else "-" for c in model_name[:30]
                    )
                    workflow_id = (
                        f"process-prompt-{input.experiment_id}-"
                        f"{prompt_idx}-{model_idx}-{safe_model_name}"
                    )

                    handle = await workflow.start_child_workflow(
                        ProcessPromptWorkflow.run,
                        ProcessPromptWorkflowInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            prompt_config=prompt_config,
                            model=model,  # Pass as-is (can be str or dict)
                            max_concurrent_rows=input.max_concurrent_rows,
                            task_queue=input.task_queue,
                        ),
                        id=workflow_id,
                        task_queue=input.task_queue,
                    )
                    child_workflow_handles.append(handle)

            # Step 3: Start base column evaluations IN PARALLEL with prompt processing
            eval_tasks = []
            for eval_template_id in eval_template_ids:
                eval_tasks.append(
                    workflow.execute_activity(
                        "run_base_evaluation_activity",
                        RunEvaluationInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            eval_template_id=eval_template_id,
                        ),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=ROW_PROCESSING_RETRY_POLICY,
                    )
                )

            # Wait for all child workflows AND evaluations to complete
            # First, gather all child workflow results
            prompt_results = []
            for handle in child_workflow_handles:
                try:
                    result = await handle
                    prompt_results.append(result)
                    total_rows_processed += get_result_field(
                        result, "completed_rows", 0
                    )
                    total_failed_rows += get_result_field(result, "failed_rows", 0)
                except Exception:
                    total_failed_rows += 1

            # Wait for base evaluations to complete (they run in parallel)
            if eval_tasks:
                await asyncio.gather(*eval_tasks, return_exceptions=True)

            # Step 4: Check and update experiment status
            status_result = await workflow.execute_activity(
                "check_experiment_status_activity",
                CheckStatusInput(
                    entity_type="experiment",
                    entity_id=input.experiment_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

            final_status = get_result_field(status_result, "status", "UNKNOWN")

            return RunExperimentOutput(
                experiment_id=input.experiment_id,
                status=final_status,
                total_rows_processed=total_rows_processed,
                failed_rows=total_failed_rows,
            )

        except Exception:
            # Cleanup: Mark any remaining running cells as error
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(experiment_id=input.experiment_id),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass

            # Mark experiment as failed in database
            try:
                await workflow.execute_activity(
                    "mark_experiment_failed_activity",
                    input.experiment_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass

            # Re-raise to mark workflow as Failed in Temporal UI
            raise


# =============================================================================
# V2 Workflows (dependency-aware, per-row eval execution)
# =============================================================================


@workflow.defn
class ProcessPromptV2Workflow(ControlledRun):
    """
    V2 child workflow to process a single ExperimentPromptConfig.

    Optimized flow with decoupled eval execution:
    1. Setup column + cells via setup_prompt_v2_activity
    2. Process rows in batches — only prompts gate batch progression
       Evals fire off in background as soon as each row's prompt completes
    3. Await all eval tasks, then check column/EDT status
    """

    @workflow.run
    async def run(
        self, input: ProcessPromptV2WorkflowInput
    ) -> ProcessPromptV2WorkflowOutput:
        await self.bind_control(
            input.experiment_id, input.experiment_prompt_config_id, "prompt"
        )
        # Step 1: Setup column + cells
        setup_result = await workflow.execute_activity(
            "setup_prompt_v2_activity",
            SetupPromptV2Input(
                experiment_id=input.experiment_id,
                dataset_id=input.dataset_id,
                experiment_prompt_config_id=input.experiment_prompt_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
                native_context=input.native_context,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(setup_result, "status") == "FAILED":
            return ProcessPromptV2WorkflowOutput(
                experiment_id=input.experiment_id,
                experiment_prompt_config_id=input.experiment_prompt_config_id,
                column_id=get_result_field(setup_result, "column_id", ""),
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="FAILED",
            )

        column_id = get_result_field(setup_result, "column_id")
        experiment_dataset_id = get_result_field(setup_result, "experiment_dataset_id")
        row_ids = get_result_field(setup_result, "row_ids", [])
        messages = get_result_field(setup_result, "messages", [])
        model_name = get_result_field(setup_result, "model_name")
        model_config = get_result_field(setup_result, "model_config", {})
        output_format = get_result_field(setup_result, "output_format")
        run_prompt_config = get_result_field(setup_result, "run_prompt_config")
        total_rows = len(row_ids)

        if total_rows == 0:
            await self.complete_control()
            return ProcessPromptV2WorkflowOutput(
                experiment_id=input.experiment_id,
                experiment_prompt_config_id=input.experiment_prompt_config_id,
                column_id=column_id,
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="COMPLETED",
            )

        # Dependent eval template IDs (passed from parent via dependency analysis)
        # Native rows seal output and all pinned evaluator outcomes together.
        # Never schedule a second, unreceipted classic evaluation for them.
        eval_template_ids = [] if input.native_context is not None else input.eval_template_ids

        # Step 2: Process rows in batches — only prompts gate batches,
        # evals are scheduled immediately via start_activity and never block prompts
        completed_count = 0
        failed_count = 0
        batch_size = input.max_concurrent_rows
        eval_handles = []

        for batch_start in range(0, total_rows, batch_size):
            await self.control_checkpoint(eval_handles)
            batch_end = min(batch_start + batch_size, total_rows)
            batch_row_ids = row_ids[batch_start:batch_end]

            prompt_tasks = []
            for row_id in batch_row_ids:
                prompt_tasks.append(
                    self._process_prompt_for_row(
                        row_id=row_id,
                        column_id=column_id,
                        dataset_id=input.dataset_id,
                        experiment_id=input.experiment_id,
                        messages=messages,
                        model=model_name,
                        model_config=model_config,
                        output_format=output_format,
                        run_prompt_config=run_prompt_config,
                        native_context=input.native_context,
                    )
                )

            batch_results = await asyncio.gather(*prompt_tasks, return_exceptions=True)

            # Schedule evals for each row:
            # - Completed rows: run eval normally
            # - Failed rows: create eval cells in ERROR state with descriptive message
            for row_id, result in zip(batch_row_ids, batch_results, strict=False):
                if not isinstance(result, Exception) and result == "COMPLETED":
                    completed_count += 1
                    if eval_template_ids:
                        for eval_template_id in eval_template_ids:
                            eval_handles.append(
                                workflow.start_activity(
                                    "process_batch_template_eval_activity",
                                    ProcessBatchEvalInput(
                                        row_ids=[row_id],
                                        column_id=column_id,
                                        dataset_id=input.dataset_id,
                                        experiment_id=input.experiment_id,
                                        eval_template_id=eval_template_id,
                                    ),
                                    start_to_close_timeout=timedelta(hours=12),
                                    heartbeat_timeout=timedelta(minutes=5),
                                    retry_policy=ROW_PROCESSING_RETRY_POLICY,
                                )
                            )
                else:
                    failed_count += 1
                    if eval_template_ids:
                        eval_handles.append(
                            workflow.start_activity(
                                "create_error_eval_cells_activity",
                                CreateErrorEvalCellsInput(
                                    failed_row_ids=[row_id],
                                    column_id=column_id,
                                    dataset_id=input.dataset_id,
                                    experiment_id=input.experiment_id,
                                    eval_template_ids=eval_template_ids,
                                ),
                                start_to_close_timeout=timedelta(minutes=5),
                                retry_policy=SETUP_RETRY_POLICY,
                            )
                        )

        await self.control_checkpoint(eval_handles)

        # All prompt rows are done — update prompt column status immediately
        # so the UI reflects completion while evals may still be running
        await workflow.execute_activity(
            "check_column_status_activity",
            CheckStatusInput(entity_type="column", entity_id=column_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=STATUS_CHECK_RETRY_POLICY,
        )

        # Await all eval handles before final status checks
        if eval_handles:
            await asyncio.gather(*eval_handles, return_exceptions=True)

        # Step 3: Check EDT status after ALL rows and evals complete
        if experiment_dataset_id:
            await workflow.execute_activity(
                "check_experiment_dataset_status_activity",
                CheckStatusInput(
                    entity_type="experiment_dataset",
                    entity_id=experiment_dataset_id,
                    native_context=input.native_context,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

        await self.complete_control()
        return ProcessPromptV2WorkflowOutput(
            experiment_id=input.experiment_id,
            experiment_prompt_config_id=input.experiment_prompt_config_id,
            column_id=column_id,
            total_rows=total_rows,
            completed_rows=completed_count,
            failed_rows=failed_count,
            status="COMPLETED" if failed_count == 0 else "PARTIAL",
        )

    async def _process_prompt_for_row(
        self,
        row_id: str,
        column_id: str,
        dataset_id: str,
        experiment_id: str,
        messages: list,
        model: str,
        model_config: dict,
        output_format,
        run_prompt_config,
        native_context=None,
    ) -> str:
        """Run the LLM prompt for a single row. Returns 'COMPLETED' or 'FAILED'."""
        row_result = await workflow.execute_activity(
            "process_row_activity",
            ProcessRowInput(
                row_id=row_id,
                column_id=column_id,
                dataset_id=dataset_id,
                experiment_id=experiment_id,
                messages=messages,
                model=model,
                model_config=model_config,
                output_format=output_format,
                run_prompt_config=run_prompt_config,
                native_context=native_context,
            ),
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=ROW_PROCESSING_RETRY_POLICY,
        )

        status = get_result_field(row_result, "status")
        return "COMPLETED" if status == "COMPLETED" else "FAILED"


@workflow.defn
class ProcessAgentWorkflow(ControlledRun):
    """
    V2 child workflow to process a single ExperimentAgentConfig.

    Optimized flow with decoupled eval execution:
    1. Setup agent columns + cells via setup_agent_activity
    2. Process rows in batches — only agent execution gates batch progression
       Evals fire off in background as soon as each row's agent completes
    3. Await all eval tasks, then check EDT status
    """

    @workflow.run
    async def run(self, input: ProcessAgentWorkflowInput) -> ProcessAgentWorkflowOutput:
        await self.bind_control(
            input.experiment_id, input.experiment_agent_config_id, "agent"
        )
        # Step 1: Setup agent columns + cells
        setup_result = await workflow.execute_activity(
            "setup_agent_activity",
            SetupAgentInput(
                experiment_id=input.experiment_id,
                dataset_id=input.dataset_id,
                experiment_agent_config_id=input.experiment_agent_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
                native_context=input.native_context,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(setup_result, 'status') == 'TERMINAL':
            rows = get_result_field(setup_result, 'row_ids', [])
            completed = get_result_field(setup_result, 'terminal_completed_rows', 0)
            return ProcessAgentWorkflowOutput(experiment_id=input.experiment_id,
                experiment_agent_config_id=input.experiment_agent_config_id, total_rows=len(rows),
                completed_rows=completed, failed_rows=len(rows)-completed,
                status='COMPLETED' if completed == len(rows) else 'PARTIAL')
        if get_result_field(setup_result, "status") == "FAILED":
            return ProcessAgentWorkflowOutput(
                experiment_id=input.experiment_id,
                experiment_agent_config_id=input.experiment_agent_config_id,
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="FAILED",
            )

        experiment_dataset_id = get_result_field(setup_result, "experiment_dataset_id")
        graph_version_id = get_result_field(setup_result, "graph_version_id")
        node_column_mapping = get_result_field(setup_result, "node_column_mapping", {})
        row_ids = get_result_field(setup_result, "row_ids", [])
        total_rows = len(row_ids)

        if total_rows == 0:
            await self.complete_control()
            return ProcessAgentWorkflowOutput(
                experiment_id=input.experiment_id,
                experiment_agent_config_id=input.experiment_agent_config_id,
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="COMPLETED",
            )

        eval_template_ids = [] if input.native_context is not None else input.eval_template_ids

        # Step 2: Process rows in batches — only agent execution gates batches,
        # evals are scheduled immediately via start_activity and never block agents
        completed_count = 0
        failed_count = 0
        batch_size = input.max_concurrent_rows
        eval_handles = []
        # Use terminal columns (end nodes) for eval; fallback to last column
        terminal_column_ids = get_result_field(setup_result, "terminal_column_ids", [])
        if not terminal_column_ids and node_column_mapping:
            terminal_column_ids = [list(node_column_mapping.values())[-1]]

        for batch_start in range(0, total_rows, batch_size):
            await self.control_checkpoint(eval_handles)
            batch_end = min(batch_start + batch_size, total_rows)
            batch_row_ids = row_ids[batch_start:batch_end]

            agent_tasks = []
            for row_id in batch_row_ids:
                agent_tasks.append(
                    self._process_agent_for_row(
                        row_id=row_id,
                        dataset_id=input.dataset_id,
                        experiment_id=input.experiment_id,
                        graph_version_id=graph_version_id,
                        node_column_mapping=node_column_mapping,
                        task_queue=input.task_queue,
                        experiment_agent_config_id=input.experiment_agent_config_id,
                        native_context=input.native_context,
                    )
                )

            batch_results = await asyncio.gather(*agent_tasks, return_exceptions=True)
            # Ordinary execution failures return only after output settlement.
            # Remaining exceptions are persistence/authorization failures.
            for result in batch_results:
                if isinstance(result, BaseException):
                    raise RuntimeError("Agent output settlement did not finish") from result

            # Schedule evals for each row:
            # - Completed rows: run eval normally against each terminal column
            # - Failed rows: create eval cells in ERROR state with descriptive message
            for row_id, result in zip(batch_row_ids, batch_results, strict=False):
                if not isinstance(result, Exception) and result == "COMPLETED":
                    completed_count += 1
                    if eval_template_ids and terminal_column_ids:
                        for eval_template_id in eval_template_ids:
                            for eval_col_id in terminal_column_ids:
                                eval_handles.append(
                                    workflow.start_activity(
                                        "process_batch_template_eval_activity",
                                        ProcessBatchEvalInput(
                                            row_ids=[row_id],
                                            column_id=eval_col_id,
                                            dataset_id=input.dataset_id,
                                            experiment_id=input.experiment_id,
                                            eval_template_id=eval_template_id,
                                        ),
                                        start_to_close_timeout=timedelta(hours=12),
                                        heartbeat_timeout=timedelta(minutes=5),
                                        retry_policy=ROW_PROCESSING_RETRY_POLICY,
                                    )
                                )
                else:
                    failed_count += 1
                    if eval_template_ids and terminal_column_ids:
                        for eval_col_id in terminal_column_ids:
                            eval_handles.append(
                                workflow.start_activity(
                                    "create_error_eval_cells_activity",
                                    CreateErrorEvalCellsInput(
                                        failed_row_ids=[row_id],
                                        column_id=eval_col_id,
                                        dataset_id=input.dataset_id,
                                        experiment_id=input.experiment_id,
                                        eval_template_ids=eval_template_ids,
                                    ),
                                    start_to_close_timeout=timedelta(minutes=5),
                                    retry_policy=SETUP_RETRY_POLICY,
                                )
                            )

        await self.control_checkpoint(eval_handles)

        # All agent rows are done — update agent output column statuses immediately
        # so the UI reflects completion while evals may still be running
        for col_id in node_column_mapping.values():
            await workflow.execute_activity(
                "check_column_status_activity",
                CheckStatusInput(entity_type="column", entity_id=str(col_id)),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

        # Await all eval handles before final status checks
        if eval_handles:
            await asyncio.gather(*eval_handles, return_exceptions=True)

        # Step 3: Check EDT status
        if experiment_dataset_id:
            await workflow.execute_activity(
                "check_experiment_dataset_status_activity",
                CheckStatusInput(
                    entity_type="experiment_dataset",
                    entity_id=experiment_dataset_id,
                    native_context=input.native_context,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

        await self.complete_control()
        return ProcessAgentWorkflowOutput(
            experiment_id=input.experiment_id,
            experiment_agent_config_id=input.experiment_agent_config_id,
            total_rows=total_rows,
            completed_rows=completed_count,
            failed_rows=failed_count,
            status="COMPLETED" if failed_count == 0 else "PARTIAL",
        )

    async def _execute_agent_for_row(
        self,
        row_id: str,
        dataset_id: str,
        experiment_id: str,
        graph_version_id: str,
        node_column_mapping: dict,
        task_queue: str = "tasks_l",
        experiment_agent_config_id: str = "",
        native_context: dict | None = None,
    ) -> str:
        """Prepare and execute graph for a single row. Returns 'COMPLETED' or 'FAILED'."""

        # Step 1: Prepare row data + create GraphExecution
        prep_result = await workflow.execute_activity(
            "prepare_agent_row_activity",
            PrepareAgentRowInput(
                row_id=row_id,
                dataset_id=dataset_id,
                experiment_id=experiment_id,
                graph_version_id=graph_version_id,
                experiment_agent_config_id=experiment_agent_config_id,
                native_context=native_context,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(prep_result, 'status') == 'TERMINAL':
            return 'FAILED' if get_result_field(prep_result, 'error') else 'COMPLETED'
        if get_result_field(prep_result, "status") != "READY":
            return "FAILED"

        graph_execution_id = get_result_field(prep_result, "graph_execution_id")
        input_payload = get_result_field(prep_result, "input_payload")

        # Step 2: Build per-node CellOutputSink overrides from node_column_mapping
        # Each LLM node gets a sink that writes its output to the corresponding cell
        node_sink_overrides = {
            node_id: [
                OutputSinkConfig(
                    name="cell",
                    config={
                        "dataset_id": dataset_id,
                        "column_id": col_id,
                        "row_id": row_id,
                        "experiment_id": experiment_id,
                    },
                )
            ]
            for node_id, col_id in node_column_mapping.items()
        }

        # Step 3: Execute graph via child workflow — sinks write cells automatically
        graph_result = await workflow.execute_child_workflow(
            "GraphExecutionWorkflow",
            GraphInput(
                graph_execution_id=graph_execution_id,
                graph_version_id=graph_version_id,
                input_payload=input_payload,
                task_queue=task_queue,
                node_sink_overrides={} if native_context is not None else node_sink_overrides,
                native_context=({'experiment_id': experiment_id, 'dataset_id': dataset_id,
                                 'row_id': row_id, 'dispatch': native_context}
                                if native_context is not None else None),
            ),
            id=(f"native-graph-{graph_execution_id}" if native_context is not None else
                f"graph-exec-{experiment_id[:8]}-{row_id[:8]}-{experiment_agent_config_id[:8]}"),
            task_queue=task_queue,
        )

        return "COMPLETED" if get_result_field(graph_result, "status") == "SUCCESS" else "FAILED"

    async def _process_agent_for_row(self, *, row_id, dataset_id, experiment_id,
                                     graph_version_id, node_column_mapping,
                                     task_queue="tasks_l", experiment_agent_config_id="",
                                     native_context=None):
        """Preparation and child transport refusals share output settlement.

        Cancellation is a BaseException and is not converted into failure here.
        Stop retains ownership of its markers and cleanup.
        """
        try:
            result = await self._execute_agent_for_row(
                row_id=row_id, dataset_id=dataset_id, experiment_id=experiment_id,
                graph_version_id=graph_version_id, node_column_mapping=node_column_mapping,
                task_queue=task_queue, experiment_agent_config_id=experiment_agent_config_id,
                native_context=native_context,
            )
        except Exception:
            result = "FAILED"
        if result != "COMPLETED":
            settled = await workflow.execute_activity(
                "create_error_agent_cells_activity",
                CreateErrorAgentCellsInput(
                    row_id=row_id, dataset_id=dataset_id, experiment_id=experiment_id,
                    node_column_mapping=node_column_mapping,
                    experiment_agent_config_id=experiment_agent_config_id,
                    native_context=native_context,
                    error_message="Agent preparation or execution failed",
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=SETUP_RETRY_POLICY,
            )
            if get_result_field(settled, "status") != "SUCCESS":
                raise RuntimeError("Agent output settlement did not finish")
            result = get_result_field(settled, "row_status") or result
        return result


@workflow.defn
class RunExperimentV2Workflow:
    """
    V2 main workflow to run an experiment.

    Dependency-aware orchestration:
    1. Setup experiment → get EPC/EAC IDs + eval template IDs
    2. Analyze eval dependencies → split into independent vs dependent
    3. Start ALL in parallel using asyncio.gather:
       - Child workflows for each EPC (with dependent eval IDs)
       - Child workflows for each EAC (with dependent eval IDs)
       - Independent eval activities (don't reference 'output')
    4. Wait for all to complete
    5. Check final experiment status
    """

    @workflow.run
    async def run(self, input: RunExperimentInput) -> RunExperimentOutput:
        http_enabled = workflow.patched("http-experiment-sources-v1")
        http_admission_id = workflow.memo().get("experiment_submission_id", "") if http_enabled else ""
        total_rows_processed = 0
        total_failed_rows = 0

        try:
            # Admission is a durable handshake, not a three-retry setup error.
            # Wait before any cleanup, control binding, cell mutation or child.
            if input.native_runs or (http_admission_id and workflow.patched("http-admission-ack-v1")):
                pending_polls = 0
                while True:
                    admission = await workflow.execute_activity(
                        "setup_experiment_activity",
                        SetupExperimentInput(
                            experiment_id=input.experiment_id,
                            native_runs=input.native_runs, admission_only=True,
                            http_admission_id=http_admission_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    phase = get_result_field(admission, "status")
                    if phase == "ADMISSION_CANCELLED":
                        return RunExperimentOutput(
                            experiment_id=input.experiment_id, status="CANCELLED",
                            total_rows_processed=0, failed_rows=0,
                        )
                    if phase == "READY":
                        break
                    if phase != "ADMISSION_PENDING":
                        raise RuntimeError("Native admission acknowledgement is unavailable")
                    pending_polls += 1
                    if pending_polls >= 120:
                        # Bound history without replacing the committed run IDs.
                        admission_id = (next(iter(input.native_runs.values()))["execution_id"]
                                        if input.native_runs else http_admission_id)
                        workflow.continue_as_new(
                            input, memo={"experiment_submission_id": admission_id},
                        )
                    await workflow.sleep(timedelta(seconds=30))

            # Step 0: Cleanup stale running cells
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(
                        experiment_id=input.experiment_id, http_admission_id=http_admission_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass

            # Detect column-change-only mode: base column changed but no
            # prompt/agent/eval changes.  Uses lightweight setup to avoid
            # resetting per-EDT eval cells.
            is_column_change_only = (
                input.column_changed
                and not input.rerun_prompt_config_ids
                and not input.rerun_agent_config_ids
                and not input.rerun_eval_template_ids
            )

            # Pre-compute selective flag: any rerun list populated means
            # this is a selective re-run from a PUT update.
            is_selective = bool(
                input.rerun_prompt_config_ids
                or input.rerun_agent_config_ids
                or input.rerun_eval_template_ids
            )

            # Step 1: Setup experiment
            if is_column_change_only or is_selective:
                # Lightweight setup — mark RUNNING without resetting eval
                # columns.  For selective re-runs the child workflows
                # (setup_prompt_v2_activity / setup_agent_activity) reset
                # only their own cells; blanket-resetting everything via
                # setup_experiment_activity would wipe cells that no
                # workflow will re-populate.
                setup_result = await workflow.execute_activity(
                    "mark_experiment_running_activity",
                    MarkExperimentRunningInput(experiment_id=input.experiment_id),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            else:
                setup_result = await workflow.execute_activity(
                    "setup_experiment_activity",
                    SetupExperimentInput(
                        experiment_id=input.experiment_id,
                        http_admission_id=(workflow.memo().get("experiment_submission_id", "") if http_enabled else ""),
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )

            if get_result_field(setup_result, "status") != "READY":
                raise Exception(
                    f"Failed to setup experiment: "
                    f"{get_result_field(setup_result, 'status')}"
                )

            prompt_config_ids = get_result_field(setup_result, "prompt_configs", [])
            agent_config_ids = get_result_field(setup_result, "agent_configs", [])
            http_source_ids = get_result_field(setup_result, "http_sources", []) if http_enabled else []
            eval_template_ids = get_result_field(setup_result, "eval_template_ids", [])
            dataset_id = get_result_field(setup_result, "dataset_id")
            has_base_column = get_result_field(setup_result, "has_base_column", True)

            # Step 1.5: Selective re-run filtering (used by V2 update flow).
            #
            # When rerun_*_ids are populated (from PUT diff), we only re-execute
            # the configs/evals that actually changed. This avoids re-running
            # the entire experiment when only a subset of configs was modified.
            #
            # Three scenarios:
            # 1. Only prompt/agent configs changed:
            #    - Re-run those configs → child workflows run ALL dependent evals
            #      (because prompt output changed, all dependent evals are stale)
            #    - Skip independent evals (they don't depend on prompt output)
            #
            # 2. Only evals changed (no config re-run):
            #    - No child workflows started (prompt/agent data already exists)
            #    - Changed evals run as independent activities against existing data
            #    - Even evals classified as "dependent" can run independently here
            #      since the output columns already have data from the previous run
            #
            # 3. Both configs and evals changed:
            #    - Re-run changed configs with ALL dependent evals
            #    - Also run new independent evals that were explicitly specified
            #
            # 4. Column changed (no other changes):
            #    - No child workflows (prompt/agent outputs are valid)
            #    - Re-run ALL base evals against the new base column
            #    - Skip per-EDT evals (prompt outputs haven't changed)
            #
            # When all rerun lists are empty and column_changed is False
            # → full run (normal flow, no filtering)
            # (is_selective already computed above before setup step)

            if is_column_change_only:
                # Column changed only — don't run prompt/agent workflows
                prompt_config_ids = []
                agent_config_ids = []
            elif is_selective:
                # Filter prompt configs: only re-run specified ones
                if input.rerun_prompt_config_ids:
                    prompt_config_ids = [
                        p
                        for p in prompt_config_ids
                        if p in input.rerun_prompt_config_ids
                    ]
                else:
                    prompt_config_ids = []

                # Filter agent configs: only re-run specified ones
                if input.rerun_agent_config_ids:
                    agent_config_ids = [
                        a for a in agent_config_ids if a in input.rerun_agent_config_ids
                    ]
                else:
                    agent_config_ids = []

            if is_column_change_only or is_selective:
                http_source_ids = []  # Classic selectors never schedule HTTP.
                # HTTP mutations enumerate their committed plan during setup.
            configs_being_rerun = bool(prompt_config_ids) or bool(agent_config_ids) or bool(http_source_ids)

            # Step 2: Analyze eval dependencies
            dependent_eval_ids = []
            independent_eval_ids = []

            if eval_template_ids:
                deps_result = await workflow.execute_activity(
                    "analyze_dependencies_activity",
                    AnalyzeDependenciesInput(
                        experiment_id=input.experiment_id,
                        eval_template_ids=eval_template_ids,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
                independent_eval_ids = get_result_field(
                    deps_result, "independent_eval_ids", []
                )
                dependent_eval_ids = get_result_field(
                    deps_result, "dependent_eval_ids", []
                )

            # Step 2.5: Apply selective eval filters after dependency analysis.
            # Pre-initialize per-EDT vars (populated only in specific paths).
            edt_column_ids = []
            edt_row_ids = []

            if input.column_changed:
                # Column changed — keep ALL eval IDs (independent + dependent)
                # as-is so ALL evals run as base evals against the new column.
                # If eval metrics also changed, resolve EDT columns so per-EDT
                # evals for changed metrics can also run.
                if (
                    is_selective
                    and not configs_being_rerun
                    and input.rerun_eval_template_ids
                ):
                    edt_cols_result = await workflow.execute_activity(
                        "resolve_edt_columns_activity",
                        ResolveEdtColumnsInput(
                            experiment_id=input.experiment_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    edt_column_ids = get_result_field(
                        edt_cols_result, "edt_column_ids", []
                    )
                    edt_row_ids = get_result_field(edt_cols_result, "row_ids", [])
            elif is_selective:
                if configs_being_rerun:
                    # Prompt/agent output is changing → keep ALL dependent evals
                    # (they'll re-run inside child workflows since output is stale).
                    # For independent evals: only run ones explicitly requested.
                    if input.rerun_eval_template_ids:
                        independent_eval_ids = [
                            e
                            for e in independent_eval_ids
                            if e in input.rerun_eval_template_ids
                        ]
                    else:
                        independent_eval_ids = []
                else:
                    # No configs re-running → experiment output data already exists.
                    # Run specified evals as independent activities (safe because
                    # the output columns they reference are already populated).
                    # Also run per-EDT evals via resolve_edt_columns_activity.
                    rerun_eval_ids_set = set(input.rerun_eval_template_ids)
                    independent_eval_ids = [
                        e for e in eval_template_ids if e in rerun_eval_ids_set
                    ]
                    dependent_eval_ids = []

                    # Resolve EDT output columns so we can run dependent evals
                    # (those referencing "output") against each prompt's output.
                    edt_cols_result = await workflow.execute_activity(
                        "resolve_edt_columns_activity",
                        ResolveEdtColumnsInput(
                            experiment_id=input.experiment_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    edt_column_ids = get_result_field(
                        edt_cols_result, "edt_column_ids", []
                    )
                    edt_row_ids = get_result_field(edt_cols_result, "row_ids", [])

            # Step 3: Start child workflows (they begin executing immediately)
            # Pass ALL eval template IDs (both dependent and independent) to
            # child workflows so per-EDT eval columns get populated for every
            # eval metric. Independent evals don't need prompt output but still
            # need per-EDT cells created. The base eval path (below) only
            # handles base-column evals; per-EDT columns are filled here.
            all_child_eval_ids = dependent_eval_ids + independent_eval_ids

            native_eval_ids = sorted(key for key, context in input.native_runs.items()
                                     if context.get("execution_mode") == "eval_only")
            dispatch_prompt_ids = prompt_config_ids + [key for key in native_eval_ids if key not in prompt_config_ids]
            graph_ids = {key for key, context in input.native_runs.items()
                         if context.get("execution_mode") == "graph"}
            if (graph_ids - set(agent_config_ids)
                    or (set(input.native_runs) - graph_ids) - set(dispatch_prompt_ids)
                    or graph_ids & set(dispatch_prompt_ids)):
                raise ValueError("An admitted native target is missing from the execution plan")
            native_output_ids = {context.get("output_column_id") for context in input.native_runs.values()}
            edt_column_ids = [column for column in edt_column_ids if column not in native_output_ids]
            prompt_handles = []
            for idx, epc_id in enumerate(dispatch_prompt_ids):
                workflow_id = (
                    f"process-prompt-v2-{input.experiment_id}-{idx}-{epc_id[:8]}"
                )
                handle = await workflow.start_child_workflow(
                    ProcessPromptV2Workflow.run,
                    ProcessPromptV2WorkflowInput(
                        experiment_id=input.experiment_id,
                        dataset_id=dataset_id,
                        experiment_prompt_config_id=epc_id,
                        native_context=input.native_runs.get(epc_id),
                        eval_template_ids=all_child_eval_ids,
                        max_concurrent_rows=input.max_concurrent_rows,
                        task_queue=input.task_queue,
                    ),
                    id=workflow_id,
                    task_queue=input.task_queue,
                )
                prompt_handles.append(handle)

            agent_handles = []
            for idx, eac_id in enumerate(agent_config_ids):
                workflow_id = f"process-agent-{input.experiment_id}-{idx}-{eac_id[:8]}"
                handle = await workflow.start_child_workflow(
                    ProcessAgentWorkflow.run,
                    ProcessAgentWorkflowInput(
                        experiment_id=input.experiment_id,
                        dataset_id=dataset_id,
                        experiment_agent_config_id=eac_id,
                        native_context=input.native_runs.get(eac_id),
                        eval_template_ids=all_child_eval_ids,
                        max_concurrent_rows=input.max_concurrent_rows,
                        task_queue=input.task_queue,
                    ),
                    id=workflow_id,
                    task_queue=input.task_queue,
                )
                agent_handles.append(handle)

            http_handles = []
            for source_id in http_source_ids:
                admission_id = workflow.memo().get("experiment_submission_id")
                if not admission_id:
                    raise ValueError("HTTP execution requires a committed submission identity")
                handle = await workflow.start_child_workflow(
                    ProcessHttpSourceWorkflow.run,
                    HttpSourceWorkflowInput(
                        experiment_id=input.experiment_id, source_id=source_id,
                        admission_id=admission_id, eval_template_ids=all_child_eval_ids,
                        max_concurrent_rows=input.max_concurrent_rows, task_queue=input.task_queue,
                    ),
                    id=f"http-source-{input.experiment_id}-{admission_id}-{source_id}",
                    task_queue=input.task_queue,
                )
                http_handles.append(handle)

            # Independent eval activity coroutines (start when gathered)
            # Only run base column evaluations if the experiment has a base column.
            independent_eval_tasks = []
            if has_base_column:
                for eval_template_id in independent_eval_ids:
                    independent_eval_tasks.append(
                        workflow.execute_activity(
                            "run_base_evaluation_activity",
                            RunEvaluationInput(
                                experiment_id=input.experiment_id,
                                dataset_id=dataset_id,
                                eval_template_id=eval_template_id,
                            ),
                            start_to_close_timeout=timedelta(hours=12),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=ROW_PROCESSING_RETRY_POLICY,
                        )
                    )

                # Also run dependent evals against the experiment's base column
                # (experiment.column). The base column data already exists in the
                # snapshot, so these can run in parallel. _run_base_evaluation_sync
                # sets runner.column to experiment.column so "output" resolves to it.
                for eval_template_id in dependent_eval_ids:
                    independent_eval_tasks.append(
                        workflow.execute_activity(
                            "run_base_evaluation_activity",
                            RunEvaluationInput(
                                experiment_id=input.experiment_id,
                                dataset_id=dataset_id,
                                eval_template_id=eval_template_id,
                            ),
                            start_to_close_timeout=timedelta(hours=12),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=ROW_PROCESSING_RETRY_POLICY,
                        )
                    )

            # Step 3.5: Eval-only re-run — run changed evals against each
            # existing EDT output column. Only applies when no configs are
            # being re-run (prompt outputs already exist).
            per_edt_eval_tasks = []
            if (
                is_selective
                and not configs_being_rerun
                and input.rerun_eval_template_ids
            ):
                for col_id in edt_column_ids:
                    for eval_tid in input.rerun_eval_template_ids:
                        per_edt_eval_tasks.append(
                            workflow.execute_activity(
                                "process_batch_template_eval_activity",
                                ProcessBatchEvalInput(
                                    row_ids=edt_row_ids,
                                    column_id=col_id,
                                    dataset_id=dataset_id,
                                    experiment_id=input.experiment_id,
                                    eval_template_id=eval_tid,
                                    # Stop between batches after a Stop.
                                    rows_per_batch=10,
                                ),
                                start_to_close_timeout=timedelta(hours=12),
                                heartbeat_timeout=timedelta(minutes=5),
                                retry_policy=ROW_PROCESSING_RETRY_POLICY,
                            )
                        )

            # Step 4: Wait for ALL in parallel via single asyncio.gather
            # Child workflow handles are already executing; eval tasks start here
            all_results = await asyncio.gather(
                *prompt_handles,
                *agent_handles,
                *http_handles,
                *independent_eval_tasks,
                *per_edt_eval_tasks,
                return_exceptions=True,
            )

            # An activity/child failure is not terminal canonical trial evidence.
            # Abort unfinished native runs rather than report a successful bridge.
            for epc_id, result in zip(dispatch_prompt_ids, all_results[:len(prompt_handles)]):
                if epc_id in input.native_runs and isinstance(result, BaseException):
                    raise RuntimeError("Native prompt evidence did not finish") from result

            # Process results — first N are prompts, next M are agents, rest are evals
            num_prompts = len(prompt_handles)
            num_agents = len(agent_handles)

            http_start = num_prompts + num_agents
            for result in all_results[http_start:http_start + len(http_handles)]:
                if isinstance(result, BaseException):
                    raise RuntimeError("HTTP source did not finish") from result
            for _i, result in enumerate(all_results[: num_prompts + num_agents + len(http_handles)]):
                if isinstance(result, Exception):
                    total_failed_rows += 1
                else:
                    total_rows_processed += get_result_field(
                        result, "completed_rows", 0
                    )
                    total_failed_rows += get_result_field(result, "failed_rows", 0)

            # Step 5: Check final experiment status
            status_result = await workflow.execute_activity(
                "check_experiment_status_activity",
                CheckStatusInput(
                    entity_type="experiment",
                    entity_id=input.experiment_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

            final_status = get_result_field(status_result, "status", "UNKNOWN")

            return RunExperimentOutput(
                experiment_id=input.experiment_id,
                status=final_status,
                total_rows_processed=total_rows_processed,
                failed_rows=total_failed_rows,
            )

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            # Shield the cleanup activity so it isn't cancelled too.
            try:
                await asyncio.shield(
                    workflow.execute_activity(
                        "stop_experiment_cleanup_activity",
                        StopExperimentCleanupInput(
                            experiment_id=input.experiment_id,
                            native_runs=input.native_runs,
                            http_admission_id=http_admission_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                )
            except Exception:
                pass

            return RunExperimentOutput(
                experiment_id=input.experiment_id,
                status="CANCELLED",
                total_rows_processed=total_rows_processed,
                failed_rows=total_failed_rows,
            )

        except Exception:
            # Cleanup: Mark remaining running cells as error
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(
                        experiment_id=input.experiment_id, native_runs=input.native_runs,
                        http_admission_id=http_admission_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass

            # Mark experiment as failed
            try:
                if not input.native_runs:
                    await workflow.execute_activity(
                        "mark_http_experiment_failed_activity" if http_admission_id else "mark_experiment_failed_activity",
                        HttpSourceInput(input.experiment_id, "", http_admission_id) if http_admission_id else input.experiment_id,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
            except Exception:
                pass

            raise


@workflow.defn
class RerunCellsV2Workflow:
    """
    Workflow for cell-level and column-level reruns.

    Unlike RunExperimentV2Workflow, this does NOT call setup_experiment_activity
    (which resets ALL eval columns). Cell and eval cell resets are handled by
    the API view before starting this workflow.

    Flow:
    1. Mark experiment RUNNING (lightweight, no eval column reset)
    2. Analyze eval dependencies
    3. Start child workflows per EPC/EAC with rerun_row_ids
    4. Wait for all to complete
    5. Check final experiment status

    A plan-bound native subset (input.native_runs) first waits, read-only, for
    its committed admission acknowledgement. Each config then schedules only
    the rows of its persisted immutable plan, resolved by its own setup
    activity, never a union of selected rows and never a failed-only re-scan.
    """

    @workflow.run
    async def run(self, input: RerunCellsV2WorkflowInput) -> RerunCellsV2WorkflowOutput:
        total_rows_processed = 0
        total_failed_rows = 0

        try:
            if input.native_runs:
                eval_ids = {key for key, context in input.native_runs.items()
                            if context.get("execution_mode") == "eval_only"}
                graph_ids = {key for key, context in input.native_runs.items()
                             if context.get("execution_mode") == "graph"}
                generated_ids = set(input.native_runs) - eval_ids - graph_ids
                if (input.base_eval_only or graph_ids != set(input.agent_config_ids)
                        or generated_ids != set(input.prompt_config_ids)
                        or graph_ids & generated_ids
                        or (input.eval_only and (generated_ids or graph_ids))
                        or (not input.eval_only and (eval_ids or input.edt_ids))):
                    raise ValueError("A native subset must contain exactly its admitted target/evaluator plan")
                first = next(iter(input.native_runs.values()))
                pending_polls = 0
                while True:
                    admission = await workflow.execute_activity(
                        "setup_experiment_activity",
                        SetupExperimentInput(
                            experiment_id=input.experiment_id,
                            native_runs=input.native_runs, admission_only=True,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    phase = get_result_field(admission, "status")
                    if phase == "ADMISSION_CANCELLED":
                        return RerunCellsV2WorkflowOutput(
                            experiment_id=input.experiment_id, status="CANCELLED",
                            total_rows_processed=0, failed_rows=0,
                        )
                    if phase == "READY":
                        break
                    if phase != "ADMISSION_PENDING":
                        raise RuntimeError("Native admission acknowledgement is unavailable")
                    pending_polls += 1
                    if pending_polls >= 120:
                        # Bound history without replacing the committed plan.
                        workflow.continue_as_new(
                            input,
                            memo={"experiment_submission_id": first["plan_id"],
                                  "execution_plan_hash": first["plan_hash"]},
                        )
                    await workflow.sleep(timedelta(seconds=30))

            # Step 1: Mark experiment RUNNING (no eval column reset)
            mark_result = await workflow.execute_activity(
                "mark_experiment_running_activity",
                MarkExperimentRunningInput(
                    experiment_id=input.experiment_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(mark_result, "status") != "READY":
                raise Exception(
                    f"Failed to mark experiment running: "
                    f"{get_result_field(mark_result, 'error', 'unknown')}"
                )

            dataset_id = input.dataset_id

            # Step 2: Analyze eval dependencies
            eval_template_ids = input.eval_template_ids
            dependent_eval_ids = []
            independent_eval_ids = []

            if eval_template_ids:
                deps_result = await workflow.execute_activity(
                    "analyze_dependencies_activity",
                    AnalyzeDependenciesInput(
                        experiment_id=input.experiment_id,
                        eval_template_ids=eval_template_ids,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
                dependent_eval_ids = get_result_field(
                    deps_result, "dependent_eval_ids", []
                )
                independent_eval_ids = get_result_field(
                    deps_result, "independent_eval_ids", []
                )

            # All evals should run in child workflows for per-EDT columns
            all_child_eval_ids = dependent_eval_ids + independent_eval_ids

            if input.eval_only:
                # Native eval-only work uses the same row receipts and finalizer;
                # its child reads pinned output, never runs prompt generation.
                eval_tasks = []
                native_eval_handles = []
                for config_id, context in sorted(input.native_runs.items()):
                    handle = await workflow.start_child_workflow(
                        ProcessPromptV2Workflow.run,
                        ProcessPromptV2WorkflowInput(
                            experiment_id=input.experiment_id, dataset_id=dataset_id,
                            experiment_prompt_config_id=config_id, native_context=context,
                            eval_template_ids=input.eval_template_ids,
                            max_concurrent_rows=input.max_concurrent_rows, task_queue=input.task_queue,
                        ),
                        id=f"rerun-eval-v2-{input.experiment_id}-{context['plan_id']}-{config_id}",
                        task_queue=input.task_queue,
                    )
                    native_eval_handles.append(handle)
                row_ids = input.row_ids

                if not input.base_eval_only:
                    # Per-EDT evals: resolve EDT columns and run
                    edt_cols_result = await workflow.execute_activity(
                        "resolve_edt_columns_activity",
                        ResolveEdtColumnsInput(
                            experiment_id=input.experiment_id,
                            edt_ids=input.edt_ids,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    edt_column_ids = get_result_field(
                        edt_cols_result, "edt_column_ids", []
                    )
                    edt_row_ids = get_result_field(edt_cols_result, "row_ids", [])
                    row_ids = input.row_ids if input.row_ids else edt_row_ids

                    native_output_ids = {context.get("output_column_id") for context in input.native_runs.values()}
                    for col_id in edt_column_ids:
                        if col_id in native_output_ids:
                            continue
                        for eval_tid in input.eval_template_ids:
                            eval_tasks.append(
                                workflow.execute_activity(
                                    "process_batch_template_eval_activity",
                                    ProcessBatchEvalInput(
                                        row_ids=row_ids,
                                        column_id=col_id,
                                        dataset_id=dataset_id,
                                        experiment_id=input.experiment_id,
                                        eval_template_id=eval_tid,
                                        # Stop between batches after a Stop.
                                        rows_per_batch=10,
                                    ),
                                    start_to_close_timeout=timedelta(hours=12),
                                    heartbeat_timeout=timedelta(minutes=5),
                                    retry_policy=ROW_PROCESSING_RETRY_POLICY,
                                )
                            )

                # Base evals
                for eval_tid in input.eval_template_ids:
                    eval_tasks.append(
                        workflow.execute_activity(
                            "run_base_evaluation_activity",
                            RunEvaluationInput(
                                experiment_id=input.experiment_id,
                                dataset_id=dataset_id,
                                eval_template_id=eval_tid,
                                row_ids=row_ids,
                            ),
                            start_to_close_timeout=timedelta(hours=12),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=ROW_PROCESSING_RETRY_POLICY,
                        )
                    )

                eval_results = await asyncio.gather(*native_eval_handles, *eval_tasks, return_exceptions=True)
                for result in eval_results[:len(native_eval_handles)]:
                    if isinstance(result, BaseException):
                        raise RuntimeError("Native evaluator evidence did not finish") from result
                    total_rows_processed += get_result_field(result, "completed_rows", 0)
                    total_failed_rows += get_result_field(result, "failed_rows", 0)

            else:
                # Step 3: Start child workflows per EPC with rerun_row_ids
                prompt_handles = []
                for idx, epc_id in enumerate(input.prompt_config_ids):
                    native_context = input.native_runs.get(epc_id)
                    if native_context is None:
                        wf_id = f"rerun-prompt-v2-{input.experiment_id}-{idx}-{epc_id[:8]}"
                        # Preserve recorded child IDs on pre-patch history replay.
                        if workflow.patched("legacy-rerun-parent-child-id-v1"):
                            wf_id = f"{wf_id}-{workflow.info().workflow_id}"
                    else:
                        # Plan identity keeps retries and later plans distinct.
                        wf_id = (
                            f"rerun-prompt-v2-{input.experiment_id}-"
                            f"{native_context['plan_id']}-{idx}-{epc_id[:8]}"
                        )
                    handle = await workflow.start_child_workflow(
                        ProcessPromptV2Workflow.run,
                        ProcessPromptV2WorkflowInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            experiment_prompt_config_id=epc_id,
                            native_context=native_context,
                            eval_template_ids=all_child_eval_ids,
                            max_concurrent_rows=input.max_concurrent_rows,
                            task_queue=input.task_queue,
                            # Native rows/failed-only were resolved at admission.
                            rerun_row_ids=[] if native_context else input.row_ids,
                            failed_only=False if native_context else input.failed_only,
                        ),
                        id=wf_id,
                        task_queue=input.task_queue,
                    )
                    prompt_handles.append(handle)

                # Step 3b: Start child workflows per EAC with rerun_row_ids
                agent_handles = []
                for idx, eac_id in enumerate(input.agent_config_ids):
                    native_context = input.native_runs.get(eac_id)
                    wf_id = (f"rerun-agent-{input.experiment_id}-{native_context['plan_id']}-{eac_id}"
                             if native_context else f"rerun-agent-{input.experiment_id}-{idx}-{eac_id[:8]}")
                    # Native plan IDs and pre-patch history IDs stay unchanged.
                    if not native_context and workflow.patched("legacy-rerun-parent-child-id-v1"):
                        wf_id = f"{wf_id}-{workflow.info().workflow_id}"
                    handle = await workflow.start_child_workflow(
                        ProcessAgentWorkflow.run,
                        ProcessAgentWorkflowInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            experiment_agent_config_id=eac_id,
                            native_context=native_context,
                            eval_template_ids=all_child_eval_ids,
                            max_concurrent_rows=input.max_concurrent_rows,
                            task_queue=input.task_queue,
                            rerun_row_ids=[] if native_context else input.row_ids,
                            failed_only=False if native_context else input.failed_only,
                        ),
                        id=wf_id,
                        task_queue=input.task_queue,
                    )
                    agent_handles.append(handle)

                # Step 4: Wait for all
                all_results = await asyncio.gather(
                    *prompt_handles,
                    *agent_handles,
                    return_exceptions=True,
                )

                # A failed child is not terminal canonical trial evidence.
                for epc_id, result in zip(
                    input.prompt_config_ids, all_results[: len(prompt_handles)]
                ):
                    if epc_id in input.native_runs and isinstance(result, BaseException):
                        raise RuntimeError("Native prompt evidence did not finish") from result

                for eac_id, result in zip(input.agent_config_ids,
                                          all_results[len(prompt_handles):len(prompt_handles) + len(agent_handles)]):
                    if eac_id in input.native_runs and isinstance(result, BaseException):
                        raise RuntimeError("Native graph evidence did not finish") from result

                for result in all_results:
                    if isinstance(result, Exception):
                        total_failed_rows += 1
                    else:
                        total_rows_processed += get_result_field(
                            result, "completed_rows", 0
                        )
                        total_failed_rows += get_result_field(result, "failed_rows", 0)

            # Step 5: Check final experiment status
            status_result = await workflow.execute_activity(
                "check_experiment_status_activity",
                CheckStatusInput(
                    entity_type="experiment",
                    entity_id=input.experiment_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

            final_status = get_result_field(status_result, "status", "UNKNOWN")

            return RerunCellsV2WorkflowOutput(
                experiment_id=input.experiment_id,
                status=final_status,
                total_rows_processed=total_rows_processed,
                failed_rows=total_failed_rows,
            )

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            # Shield the cleanup activity so it isn't cancelled too.
            try:
                await asyncio.shield(
                    workflow.execute_activity(
                        "stop_experiment_cleanup_activity",
                        StopExperimentCleanupInput(
                            experiment_id=input.experiment_id,
                            native_runs=input.native_runs,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                )
            except Exception:
                pass

            return RerunCellsV2WorkflowOutput(
                experiment_id=input.experiment_id,
                status="CANCELLED",
                total_rows_processed=total_rows_processed,
                failed_rows=total_failed_rows,
            )

        except Exception:
            if input.native_runs:
                # Abort unfinished native runs; never report partial evidence
                # as a successful bridge. An unconfirmed draft stays untouched.
                try:
                    await workflow.execute_activity(
                        "cleanup_running_cells_activity",
                        CleanupRunningCellsInput(
                            experiment_id=input.experiment_id,
                            native_runs=input.native_runs,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                except Exception:
                    pass

            # On failure, mark experiment as failed
            try:
                if not input.native_runs:
                    await workflow.execute_activity(
                        "mark_experiment_failed_activity",
                        input.experiment_id,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
            except Exception:
                pass

            raise


# Export workflows
__all__ = [
    "RunExperimentInput",
    "RunExperimentOutput",
    "ProcessPromptWorkflowInput",
    "ProcessPromptWorkflowOutput",
    "ProcessPromptWorkflow",
    "RunExperimentWorkflow",
    # V2
    "ProcessPromptV2WorkflowInput",
    "ProcessPromptV2WorkflowOutput",
    "ProcessPromptV2Workflow",
    "ProcessAgentWorkflowInput",
    "ProcessAgentWorkflowOutput",
    "ProcessAgentWorkflow",
    "RunExperimentV2Workflow",
    # Rerun cells
    "RerunCellsV2Workflow",
    "RerunCellsV2WorkflowInput",
    "RerunCellsV2WorkflowOutput",
]


# A timeout or worker loss can follow an externally committed POST. Activity
# delivery retries must never turn an unknown outcome into another request.
HTTP_REQUEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class ProcessHttpSourceWorkflow:
    @workflow.run
    async def run(self, input: HttpSourceWorkflowInput) -> dict:
        identity = HttpSourceInput(input.experiment_id, input.source_id, input.admission_id)
        plan = await workflow.execute_activity(
            "setup_http_source_activity", identity,
            start_to_close_timeout=timedelta(minutes=2), retry_policy=SETUP_RETRY_POLICY,
        )
        column_id = get_result_field(plan, "column_id")
        dataset_id = get_result_field(plan, "dataset_id")
        row_ids = get_result_field(plan, "row_ids", [])
        http_context = {
            "experiment_id": input.experiment_id, "source_id": input.source_id,
            "admission_id": input.admission_id, "dataset_id": dataset_id,
            "column_id": column_id,
        }
        width = max(1, min(input.max_concurrent_rows, 32))

        async def run_row(row_id):
            request = HttpRowInput(input.experiment_id, input.source_id, input.admission_id, row_id)
            try:
                result = await workflow.execute_activity(
                    "process_http_row_activity", request,
                    start_to_close_timeout=timedelta(seconds=90),
                    retry_policy=HTTP_REQUEST_RETRY_POLICY,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = {"status": "BLOCKED"}
            if get_result_field(result, "status") == "BLOCKED":
                # A duplicate is not success. Recover only a committed terminal
                # result, or record an unknown outcome without calling HTTP.
                result = await workflow.execute_activity(
                    "settle_http_row_activity", request,
                    start_to_close_timeout=timedelta(minutes=2), retry_policy=SETUP_RETRY_POLICY,
                )
            if get_result_field(result, "status") not in {"COMPLETED", "FAILED"}:
                raise RuntimeError("HTTP attempt has no terminal evidence")
            return row_id, get_result_field(result, "status")

        for offset in range(0, len(row_ids), width):
            outcomes = await asyncio.gather(*(run_row(row) for row in row_ids[offset:offset + width]))
            succeeded = [row for row, status in outcomes if status == "COMPLETED"]
            failed = [row for row, status in outcomes if status == "FAILED"]
            for eval_id in input.eval_template_ids:
                if succeeded:
                    result = await workflow.execute_activity(
                        "process_batch_template_eval_activity",
                        ProcessBatchEvalInput(succeeded, column_id, dataset_id, input.experiment_id, eval_id,
                                              http_context=http_context),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5), retry_policy=ROW_PROCESSING_RETRY_POLICY,
                    )
                    if get_result_field(result, "status") != "COMPLETED":
                        raise RuntimeError("HTTP output evaluation did not complete")
            if failed and input.eval_template_ids:
                result = await workflow.execute_activity(
                    "create_error_eval_cells_activity",
                    CreateErrorEvalCellsInput(
                        failed, column_id, dataset_id, input.experiment_id, input.eval_template_ids,
                        "Evaluation skipped: HTTP source failed. Check the row error before rerunning.",
                        http_context=http_context,
                    ),
                    start_to_close_timeout=timedelta(minutes=5), retry_policy=SETUP_RETRY_POLICY,
                )
                if get_result_field(result, "status") != "COMPLETED":
                    raise RuntimeError("HTTP failed-row policy did not settle")
        result = await workflow.execute_activity(
            "finish_http_source_activity", identity,
            start_to_close_timeout=timedelta(minutes=5), retry_policy=SETUP_RETRY_POLICY,
        )
        await workflow.execute_activity(
            "check_experiment_dataset_status_activity",
            CheckStatusInput(entity_type="experiment_dataset", entity_id=input.source_id),
            start_to_close_timeout=timedelta(minutes=2), retry_policy=STATUS_CHECK_RETRY_POLICY,
        )
        return result
