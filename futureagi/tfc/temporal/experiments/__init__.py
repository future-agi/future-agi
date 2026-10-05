"""
Temporal workflows and activities for experiments.

This module handles experiment execution via Temporal.

IMPORTANT: Only types.py and workflows.py are imported at module level.
Everything else is lazy-loaded to avoid Django imports during workflow
sandbox validation.
"""

# Import types (no Django dependencies, safe for workflows)
from tfc.temporal.experiments.types import (  # V2 types; Rerun cells types; Error eval cells types; Error agent cells types
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
    ProcessAgentWorkflowInput,
    ProcessAgentWorkflowOutput,
    ProcessBatchEvalInput,
    ProcessBatchEvalOutput,
    ProcessPromptInput,
    ProcessPromptOutput,
    ProcessPromptV2WorkflowInput,
    ProcessPromptV2WorkflowOutput,
    ProcessPromptWorkflowInput,
    ProcessPromptWorkflowOutput,
    ProcessRowEvalInput,
    ProcessRowEvalOutput,
    ProcessRowInput,
    ProcessRowOutput,
    RerunCellsV2WorkflowInput,
    RerunCellsV2WorkflowOutput,
    RunEvaluationInput,
    RunEvaluationOutput,
    RunExperimentInput,
    RunExperimentOutput,
    SetupAgentInput,
    SetupAgentOutput,
    SetupExperimentInput,
    SetupExperimentOutput,
    SetupPromptV2Input,
    SetupPromptV2Output,
    FailEvalOnlyRerunInput,
    WaitForExperimentRunsInput,
    WaitForExperimentRunsOutput,
)

# Import workflows (no Django dependencies)
from tfc.temporal.experiments.workflows import (
    ProcessAgentWorkflow,
    ProcessPromptV2Workflow,
    ProcessPromptWorkflow,
    RerunCellsV2Workflow,
    RunExperimentV2Workflow,
    RunExperimentWorkflow,
)


def get_activities():
    """
    Get activity functions. Import lazily to avoid Django imports at module level.
    """
    from tfc.temporal.experiments.activities import (
        analyze_dependencies_activity,
        check_column_status_activity,
        check_experiment_dataset_status_activity,
        check_experiment_status_activity,
        cleanup_running_cells_activity,
        create_error_agent_cells_activity,
        create_error_eval_cells_activity,
        get_eval_templates_activity,
        mark_experiment_failed_activity,
        mark_experiment_running_activity,
        prepare_agent_row_activity,
        process_batch_template_eval_activity,
        process_prompt_activity,
        process_row_activity,
        process_row_eval_activity,
        resolve_edt_columns_activity,
        run_base_evaluation_activity,
        setup_agent_activity,
        setup_experiment_activity,
        setup_prompt_v2_activity,
        stop_experiment_cleanup_activity,
        wait_for_experiment_runs_activity,
        fail_eval_only_rerun_activity,
    )

    return [
        setup_experiment_activity,
        process_prompt_activity,
        setup_prompt_v2_activity,
        setup_agent_activity,
        prepare_agent_row_activity,
        process_row_activity,
        process_row_eval_activity,
        get_eval_templates_activity,
        process_batch_template_eval_activity,
        run_base_evaluation_activity,
        check_column_status_activity,
        check_experiment_dataset_status_activity,
        check_experiment_status_activity,
        mark_experiment_failed_activity,
        mark_experiment_running_activity,
        cleanup_running_cells_activity,
        analyze_dependencies_activity,
        resolve_edt_columns_activity,
        stop_experiment_cleanup_activity,
        create_error_eval_cells_activity,
        create_error_agent_cells_activity,
        wait_for_experiment_runs_activity,
        fail_eval_only_rerun_activity,
    ]


def get_workflows():
    """Get workflow classes."""
    return [
        RunExperimentWorkflow,
        ProcessPromptWorkflow,
        # V2
        RunExperimentV2Workflow,
        ProcessPromptV2Workflow,
        ProcessAgentWorkflow,
        # Rerun cells
        RerunCellsV2Workflow,
    ]


# Lazy-loaded client functions
def start_experiment_workflow(*args, **kwargs):
    """Start an experiment workflow synchronously."""
    from tfc.temporal.experiments.client import start_experiment_workflow as _start

    return _start(*args, **kwargs)


def start_experiment_workflow_async(*args, **kwargs):
    """Start an experiment workflow asynchronously."""
    from tfc.temporal.experiments.client import (
        start_experiment_workflow_async as _start,
    )

    return _start(*args, **kwargs)


def start_experiment_v2_workflow(*args, **kwargs):
    """Start a V2 experiment workflow synchronously."""
    from tfc.temporal.experiments.client import start_experiment_v2_workflow as _start

    return _start(*args, **kwargs)


def start_experiment_v2_workflow_async(*args, **kwargs):
    """Start a V2 experiment workflow asynchronously."""
    from tfc.temporal.experiments.client import (
        start_experiment_v2_workflow_async as _start,
    )

    return _start(*args, **kwargs)


def get_experiment_workflow_status(*args, **kwargs):
    """Get experiment workflow status synchronously."""
    from tfc.temporal.experiments.client import get_experiment_workflow_status as _get

    return _get(*args, **kwargs)


def get_experiment_workflow_status_async(*args, **kwargs):
    """Get experiment workflow status asynchronously."""
    from tfc.temporal.experiments.client import (
        get_experiment_workflow_status_async as _get,
    )

    return _get(*args, **kwargs)


def cancel_experiment_workflow(*args, **kwargs):
    """Cancel experiment workflow synchronously."""
    from tfc.temporal.experiments.client import cancel_experiment_workflow as _cancel

    return _cancel(*args, **kwargs)


def cancel_experiment_workflow_async(*args, **kwargs):
    """Cancel experiment workflow asynchronously."""
    from tfc.temporal.experiments.client import (
        cancel_experiment_workflow_async as _cancel,
    )

    return _cancel(*args, **kwargs)


def cancel_all_experiment_workflows(*args, **kwargs):
    """Cancel ALL running workflows for an experiment (main + reruns) synchronously."""
    from tfc.temporal.experiments.client import (
        cancel_all_experiment_workflows as _cancel_all,
    )

    return _cancel_all(*args, **kwargs)


def cancel_all_experiment_workflows_async(*args, **kwargs):
    """Cancel ALL running workflows for an experiment (main + reruns) asynchronously."""
    from tfc.temporal.experiments.client import (
        cancel_all_experiment_workflows_async as _cancel_all,
    )

    return _cancel_all(*args, **kwargs)


def start_rerun_cells_v2_workflow(*args, **kwargs):
    """Start a cell/column-level rerun workflow synchronously."""
    from tfc.temporal.experiments.client import start_rerun_cells_v2_workflow as _start

    return _start(*args, **kwargs)


def start_rerun_cells_v2_workflow_async(*args, **kwargs):
    """Start a cell/column-level rerun workflow asynchronously."""
    from tfc.temporal.experiments.client import (
        start_rerun_cells_v2_workflow_async as _start,
    )

    return _start(*args, **kwargs)


def start_experiment_eval_rerun_workflow(*args, **kwargs):
    """Start a non-cancelling eval-only rerun synchronously."""
    from tfc.temporal.experiments.client import (
        start_experiment_eval_rerun_workflow as _start,
    )

    return _start(*args, **kwargs)


def start_experiment_eval_rerun_workflow_async(*args, **kwargs):
    """Start a non-cancelling eval-only rerun asynchronously."""
    from tfc.temporal.experiments.client import (
        start_experiment_eval_rerun_workflow_async as _start,
    )

    return _start(*args, **kwargs)


__all__ = [
    # Types (activity inputs/outputs)
    "SetupExperimentInput",
    "SetupExperimentOutput",
    "ProcessPromptInput",
    "ProcessPromptOutput",
    "ProcessRowInput",
    "ProcessRowOutput",
    "ProcessRowEvalInput",
    "ProcessRowEvalOutput",
    "GetEvalTemplatesInput",
    "GetEvalTemplatesOutput",
    "ProcessBatchEvalInput",
    "ProcessBatchEvalOutput",
    "RunEvaluationInput",
    "RunEvaluationOutput",
    "CheckStatusInput",
    "CheckStatusOutput",
    "CleanupRunningCellsInput",
    "CleanupRunningCellsOutput",
    # Types (workflow inputs/outputs)
    "RunExperimentInput",
    "RunExperimentOutput",
    "ProcessPromptWorkflowInput",
    "ProcessPromptWorkflowOutput",
    # Workflows
    "RunExperimentWorkflow",
    "ProcessPromptWorkflow",
    # V2 Workflows
    "RunExperimentV2Workflow",
    "ProcessPromptV2Workflow",
    "ProcessAgentWorkflow",
    # V2 Types
    "SetupPromptV2Input",
    "SetupPromptV2Output",
    "SetupAgentInput",
    "SetupAgentOutput",
    "PrepareAgentRowInput",
    "PrepareAgentRowOutput",
    "ProcessAgentWorkflowInput",
    "ProcessAgentWorkflowOutput",
    "AnalyzeDependenciesInput",
    "AnalyzeDependenciesOutput",
    # Client helpers (lazy-loaded)
    "start_experiment_workflow",
    "start_experiment_workflow_async",
    "start_experiment_v2_workflow",
    "start_experiment_v2_workflow_async",
    "get_experiment_workflow_status",
    "get_experiment_workflow_status_async",
    "cancel_experiment_workflow",
    "cancel_experiment_workflow_async",
    "cancel_all_experiment_workflows",
    "cancel_all_experiment_workflows_async",
    "start_rerun_cells_v2_workflow",
    "start_rerun_cells_v2_workflow_async",
    "start_experiment_eval_rerun_workflow",
    "start_experiment_eval_rerun_workflow_async",
    # Rerun cells types & workflow
    "MarkExperimentRunningInput",
    "MarkExperimentRunningOutput",
    "FailEvalOnlyRerunInput",
    "WaitForExperimentRunsInput",
    "WaitForExperimentRunsOutput",
    "RerunCellsV2WorkflowInput",
    "RerunCellsV2WorkflowOutput",
    "RerunCellsV2Workflow",
    # Error eval cells types
    "CreateErrorEvalCellsInput",
    "CreateErrorEvalCellsOutput",
    # Error agent cells types
    "CreateErrorAgentCellsInput",
    "CreateErrorAgentCellsOutput",
    # Lazy getters
    "get_activities",
    "get_workflows",
]
