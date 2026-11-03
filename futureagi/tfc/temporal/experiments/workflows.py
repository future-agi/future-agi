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
    from tfc.temporal.experiments.types import (  # V2 types; Rerun cells types; Stop experiment types; Error eval cells types; Error agent cells types
        AnalyzeDependenciesInput,
        CheckStatusInput,
        CleanupRunningCellsInput,
        CreateErrorAgentCellsInput,
        CreateErrorEvalCellsInput,
        FailEvalOnlyRerunInput,
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
        WaitForExperimentRunsInput,
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

# Waiting can span worker restarts; bound total time via schedule-to-close,
# not a small per-attempt setup retry budget.
WAIT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=0,
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
            for row_id, result in zip(batch_row_ids, batch_results):
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
            all_tasks = []

            # Step 2: Start child workflows for each prompt_config x model combination
            child_workflow_handles = []
            for prompt_idx, prompt_config in enumerate(prompt_configs):
                prompt_name = prompt_config.get("name", f"prompt-{prompt_idx}")
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

        except Exception as e:
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
class ProcessPromptV2Workflow:
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
        # Step 1: Setup column + cells
        setup_result = await workflow.execute_activity(
            "setup_prompt_v2_activity",
            SetupPromptV2Input(
                experiment_id=input.experiment_id,
                dataset_id=input.dataset_id,
                experiment_prompt_config_id=input.experiment_prompt_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
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
        eval_template_ids = input.eval_template_ids

        # Step 2: Process rows in batches — only prompts gate batches,
        # evals are scheduled immediately via start_activity and never block prompts
        completed_count = 0
        failed_count = 0
        batch_size = input.max_concurrent_rows
        eval_handles = []

        for batch_start in range(0, total_rows, batch_size):
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
                    )
                )

            batch_results = await asyncio.gather(*prompt_tasks, return_exceptions=True)

            # Schedule evals for each row:
            # - Completed rows: run eval normally
            # - Failed rows: create eval cells in ERROR state with descriptive message
            for row_id, result in zip(batch_row_ids, batch_results):
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
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

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
            ),
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=ROW_PROCESSING_RETRY_POLICY,
        )

        status = get_result_field(row_result, "status")
        return "COMPLETED" if status == "COMPLETED" else "FAILED"


@workflow.defn
class ProcessAgentWorkflow:
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
        # Step 1: Setup agent columns + cells
        setup_result = await workflow.execute_activity(
            "setup_agent_activity",
            SetupAgentInput(
                experiment_id=input.experiment_id,
                dataset_id=input.dataset_id,
                experiment_agent_config_id=input.experiment_agent_config_id,
                rerun_row_ids=input.rerun_row_ids,
                failed_only=input.failed_only,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

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
            return ProcessAgentWorkflowOutput(
                experiment_id=input.experiment_id,
                experiment_agent_config_id=input.experiment_agent_config_id,
                total_rows=0,
                completed_rows=0,
                failed_rows=0,
                status="COMPLETED",
            )

        eval_template_ids = input.eval_template_ids

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
                    )
                )

            batch_results = await asyncio.gather(*agent_tasks, return_exceptions=True)

            # Schedule evals for each row:
            # - Completed rows: run eval normally against each terminal column
            # - Failed rows: create eval cells in ERROR state with descriptive message
            for row_id, result in zip(batch_row_ids, batch_results):
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
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STATUS_CHECK_RETRY_POLICY,
            )

        return ProcessAgentWorkflowOutput(
            experiment_id=input.experiment_id,
            experiment_agent_config_id=input.experiment_agent_config_id,
            total_rows=total_rows,
            completed_rows=completed_count,
            failed_rows=failed_count,
            status="COMPLETED" if failed_count == 0 else "PARTIAL",
        )

    async def _process_agent_for_row(
        self,
        row_id: str,
        dataset_id: str,
        experiment_id: str,
        graph_version_id: str,
        node_column_mapping: dict,
        task_queue: str = "tasks_l",
        experiment_agent_config_id: str = "",
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
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=SETUP_RETRY_POLICY,
        )

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
                node_sink_overrides=node_sink_overrides,
            ),
            id=f"graph-exec-{experiment_id[:8]}-{row_id[:8]}-{experiment_agent_config_id[:8]}",
            task_queue=task_queue,
        )

        graph_status = get_result_field(graph_result, "status")
        if graph_status != "SUCCESS":
            # Write error cells for agent output columns still in RUNNING state
            await workflow.execute_activity(
                "create_error_agent_cells_activity",
                CreateErrorAgentCellsInput(
                    row_id=row_id,
                    dataset_id=dataset_id,
                    experiment_id=experiment_id,
                    node_column_mapping=node_column_mapping,
                    error_message=get_result_field(
                        graph_result, "error", "Agent execution failed"
                    ),
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=SETUP_RETRY_POLICY,
            )
            return "FAILED"
        return "COMPLETED"


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
        total_rows_processed = 0
        total_failed_rows = 0

        try:
            # Step 0: Cleanup stale running cells
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(experiment_id=input.experiment_id),
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
                    SetupExperimentInput(experiment_id=input.experiment_id),
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

            configs_being_rerun = bool(prompt_config_ids) or bool(agent_config_ids)

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

            prompt_handles = []
            for idx, epc_id in enumerate(prompt_config_ids):
                workflow_id = (
                    f"process-prompt-v2-{input.experiment_id}-{idx}-{epc_id[:8]}"
                )
                handle = await workflow.start_child_workflow(
                    ProcessPromptV2Workflow.run,
                    ProcessPromptV2WorkflowInput(
                        experiment_id=input.experiment_id,
                        dataset_id=dataset_id,
                        experiment_prompt_config_id=epc_id,
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
                        eval_template_ids=all_child_eval_ids,
                        max_concurrent_rows=input.max_concurrent_rows,
                        task_queue=input.task_queue,
                    ),
                    id=workflow_id,
                    task_queue=input.task_queue,
                )
                agent_handles.append(handle)

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
                *independent_eval_tasks,
                *per_edt_eval_tasks,
                return_exceptions=True,
            )

            # Process results — first N are prompts, next M are agents, rest are evals
            num_prompts = len(prompt_handles)
            num_agents = len(agent_handles)

            for i, result in enumerate(all_results[: num_prompts + num_agents]):
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

        except Exception as e:
            # Cleanup: Mark remaining running cells as error
            try:
                await workflow.execute_activity(
                    "cleanup_running_cells_activity",
                    CleanupRunningCellsInput(experiment_id=input.experiment_id),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception:
                pass

            # Mark experiment as failed
            try:
                await workflow.execute_activity(
                    "mark_experiment_failed_activity",
                    input.experiment_id,
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
    """

    @workflow.run
    async def run(self, input: RerunCellsV2WorkflowInput) -> RerunCellsV2WorkflowOutput:
        total_rows_processed = 0
        total_failed_rows = 0

        try:
            if input.wait_for_inflight_runs:
                wait_result = await workflow.execute_activity(
                    "wait_for_experiment_runs_activity",
                    WaitForExperimentRunsInput(
                        experiment_id=input.experiment_id,
                        exclude_workflow_id=workflow.info().workflow_id,
                    ),
                    schedule_to_close_timeout=timedelta(hours=6, minutes=5),
                    start_to_close_timeout=timedelta(hours=6),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=WAIT_RETRY_POLICY,
                )
                if get_result_field(wait_result, "status") == "CANCELLED":
                    return RerunCellsV2WorkflowOutput(
                        experiment_id=input.experiment_id,
                        status="CANCELLED",
                        total_rows_processed=0,
                        failed_rows=0,
                    )
                if get_result_field(wait_result, "status") != "READY":
                    await workflow.execute_activity(
                        "fail_eval_only_rerun_activity",
                        FailEvalOnlyRerunInput(
                            experiment_id=input.experiment_id,
                            eval_template_ids=input.eval_template_ids,
                            reason="Timed out waiting for final outputs. Retry evaluation.",
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    return RerunCellsV2WorkflowOutput(
                        experiment_id=input.experiment_id,
                        status="FAILED",
                        total_rows_processed=0,
                        failed_rows=0,
                    )

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
                # Step 3 (eval-only): Run eval activities directly, no child workflows
                eval_tasks = []
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

                    for col_id in edt_column_ids:
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

                await asyncio.gather(*eval_tasks, return_exceptions=True)

            else:
                # Step 3: Start child workflows per EPC with rerun_row_ids
                prompt_handles = []
                for idx, epc_id in enumerate(input.prompt_config_ids):
                    wf_id = f"rerun-prompt-v2-{input.experiment_id}-{idx}-{epc_id[:8]}"
                    handle = await workflow.start_child_workflow(
                        ProcessPromptV2Workflow.run,
                        ProcessPromptV2WorkflowInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            experiment_prompt_config_id=epc_id,
                            eval_template_ids=all_child_eval_ids,
                            max_concurrent_rows=input.max_concurrent_rows,
                            task_queue=input.task_queue,
                            rerun_row_ids=input.row_ids,
                            failed_only=input.failed_only,
                        ),
                        id=wf_id,
                        task_queue=input.task_queue,
                    )
                    prompt_handles.append(handle)

                # Step 3b: Start child workflows per EAC with rerun_row_ids
                agent_handles = []
                for idx, eac_id in enumerate(input.agent_config_ids):
                    wf_id = f"rerun-agent-{input.experiment_id}-{idx}-{eac_id[:8]}"
                    handle = await workflow.start_child_workflow(
                        ProcessAgentWorkflow.run,
                        ProcessAgentWorkflowInput(
                            experiment_id=input.experiment_id,
                            dataset_id=dataset_id,
                            experiment_agent_config_id=eac_id,
                            eval_template_ids=all_child_eval_ids,
                            max_concurrent_rows=input.max_concurrent_rows,
                            task_queue=input.task_queue,
                            rerun_row_ids=input.row_ids,
                            failed_only=input.failed_only,
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
            try:
                if input.wait_for_inflight_runs:
                    # The main producer is a separate workflow. Do not fail it
                    # or leave this eval's placeholders spinning indefinitely.
                    await workflow.execute_activity(
                        "fail_eval_only_rerun_activity",
                        FailEvalOnlyRerunInput(
                            experiment_id=input.experiment_id,
                            eval_template_ids=input.eval_template_ids,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                else:
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
