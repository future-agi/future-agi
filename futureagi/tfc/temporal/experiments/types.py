"""
Data classes for experiment workflows and activities.

This file is separate from activities.py to avoid Django imports
when Temporal validates workflows in its sandbox.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# =============================================================================
# Activity Input/Output Data Classes
# =============================================================================


@dataclass
class ProcessRowInput:
    """Input for processing a single row."""

    row_id: str
    column_id: str
    dataset_id: str
    experiment_id: str
    messages: list
    model: str
    model_config: dict
    output_format: Optional[str] = None
    run_prompt_config: Optional[dict] = None


@dataclass
class ProcessRowOutput:
    """Output from processing a row."""

    row_id: str
    column_id: str
    status: str  # "COMPLETED" or "FAILED"
    error: Optional[str] = None


@dataclass
class SetupExperimentInput:
    """Input for setting up an experiment."""

    experiment_id: str


@dataclass
class SetupExperimentOutput:
    """Output from setting up an experiment."""

    experiment_id: str
    dataset_id: str
    prompt_configs: list  # List of prompt configurations (V1: from JSON, V2: from EPC)
    eval_template_ids: list  # List of evaluation template IDs
    status: str
    agent_configs: list = None  # V2 only: list of agent config dicts
    experiment_type: str = "llm"  # V2: experiment type (llm, tts, stt, image)
    has_base_column: bool = True  # Whether experiment has a base column


@dataclass
class ProcessPromptInput:
    """Input for processing a prompt configuration."""

    experiment_id: str
    dataset_id: str
    prompt_config: dict
    model: Any  # Can be string or dict (ModelSpec) - use Any for Temporal serialization


@dataclass
class ProcessPromptOutput:
    """Output from processing a prompt."""

    experiment_id: str
    column_id: str
    experiment_dataset_id: str  # ID of the experiment_dataset for status updates
    row_ids: list  # List of row IDs to process
    messages: list  # Messages template for row processing
    model_name: str  # Extracted model name
    model_config: dict  # Model configuration
    output_format: Optional[str]
    run_prompt_config: Optional[dict]  # Model-specific run config
    status: str
    error: Optional[str] = None


@dataclass
class RunEvaluationInput:
    """Input for running an evaluation."""

    experiment_id: str
    dataset_id: str
    eval_template_id: str
    row_ids: List[str] = field(default_factory=list)  # Empty = all rows


@dataclass
class RunEvaluationOutput:
    """Output from running an evaluation."""

    experiment_id: str
    eval_template_id: str
    status: str
    error: Optional[str] = None


@dataclass
class CheckStatusInput:
    """Input for checking status."""

    entity_type: str  # "column", "experiment_dataset", or "experiment"
    entity_id: str


@dataclass
class CheckStatusOutput:
    """Output from checking status."""

    entity_id: str
    is_complete: bool
    status: str


# =============================================================================
# Workflow Input/Output Data Classes
# =============================================================================


@dataclass
class RunExperimentInput:
    """Input for running an experiment.

    Selective re-run fields (used by V2 update flow):
    - rerun_prompt_config_ids: if non-empty, only process these EPC IDs
    - rerun_agent_config_ids: if non-empty, only process these EAC IDs
    - rerun_eval_template_ids: if non-empty, only run these eval IDs
    - column_changed: if True, base column changed — re-run base evals only
    When all three rerun lists are empty and column_changed is False, runs the full experiment.
    """

    experiment_id: str
    max_concurrent_rows: int = 10  # Max concurrent row processing per prompt
    task_queue: str = "tasks_l"  # Task queue for child workflows and activities
    rerun_prompt_config_ids: List[str] = field(default_factory=list)
    rerun_agent_config_ids: List[str] = field(default_factory=list)
    rerun_eval_template_ids: List[str] = field(default_factory=list)
    column_changed: bool = False


@dataclass
class RunExperimentOutput:
    """Output from running an experiment."""

    experiment_id: str
    status: str  # "COMPLETED" or "FAILED"
    total_rows_processed: int
    failed_rows: int
    error: Optional[str] = None


@dataclass
class ProcessPromptWorkflowInput:
    """Input for processing a single prompt configuration."""

    experiment_id: str
    dataset_id: str
    prompt_config: Dict[str, Any]  # Prompt configuration dict
    model: Any  # Can be string OR dict (ModelSpec) - use Any for Temporal serialization
    max_concurrent_rows: int = 10
    task_queue: str = "tasks_l"  # Task queue for activities


@dataclass
class ProcessPromptWorkflowOutput:
    """Output from processing a prompt."""

    experiment_id: str
    column_id: str
    total_rows: int
    completed_rows: int
    failed_rows: int
    status: str


@dataclass
class CleanupRunningCellsInput:
    """Input for cleaning up running cells after workflow failure/termination."""

    experiment_id: str


@dataclass
class CleanupRunningCellsOutput:
    """Output from cleaning up running cells."""

    experiment_id: str
    cells_cleaned: int
    status: str
    error: Optional[str] = None


@dataclass
class ProcessRowEvalInput:
    """Input for processing row-wise evaluations."""

    row_id: str
    column_id: str
    dataset_id: str
    experiment_id: str


@dataclass
class ProcessRowEvalOutput:
    """Output from processing row-wise evaluations."""

    row_id: str
    column_id: str
    evals_triggered: int
    status: str
    error: Optional[str] = None


@dataclass
class GetEvalTemplatesInput:
    """Input for getting eval template IDs for an experiment."""

    column_id: str
    experiment_id: str


@dataclass
class GetEvalTemplatesOutput:
    """Output with eval template IDs."""

    eval_template_ids: List[str]


@dataclass
class ProcessBatchEvalInput:
    """Input for processing a batch of rows for a single eval template."""

    row_ids: List[str]  # Batch of row IDs
    column_id: str
    dataset_id: str
    experiment_id: str
    eval_template_id: str


@dataclass
class ProcessBatchEvalOutput:
    """Output from batch eval processing."""

    row_ids: List[str]
    column_id: str
    eval_template_id: str
    status: str
    error: Optional[str] = None


# =============================================================================
# V2 Activity Input/Output Data Classes
# =============================================================================


@dataclass
class WaitForExperimentRunsInput:
    """Input for waiting until an experiment's output-producing runs close."""

    experiment_id: str
    exclude_workflow_id: str = ""
    poll_interval_seconds: float = 5.0
    max_wait_seconds: float = 6 * 3600


@dataclass
class WaitForExperimentRunsOutput:
    """Outcome of waiting for output-producing experiment runs."""

    status: str
    waited_seconds: float = 0.0


@dataclass
class MarkExperimentRunningInput:
    """Input for marking experiment as RUNNING without resetting eval columns."""

    experiment_id: str


@dataclass
class MarkExperimentRunningOutput:
    """Output from marking experiment RUNNING."""

    experiment_id: str
    dataset_id: str
    eval_template_ids: List[str]
    status: str
    prompt_configs: List[str] = field(default_factory=list)
    agent_configs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    has_base_column: bool = True


@dataclass
class SetupPromptV2Input:
    """Input for setting up a single EPC-based prompt (V2)."""

    experiment_id: str
    dataset_id: str
    experiment_prompt_config_id: str  # ExperimentPromptConfig.id
    rerun_row_ids: List[str] = field(default_factory=list)  # Empty = all rows
    failed_only: bool = False  # If true, only rerun ERROR cells


@dataclass
class SetupPromptV2Output:
    """Output from setting up a V2 prompt — column + rows ready."""

    experiment_id: str
    experiment_prompt_config_id: str
    column_id: str
    experiment_dataset_id: str
    row_ids: List[str]
    messages: list
    model_name: str
    model_config: dict
    output_format: Optional[str]
    run_prompt_config: Optional[dict]
    status: str
    error: Optional[str] = None


@dataclass
class SetupAgentInput:
    """Input for setting up an agent experiment entry (V2)."""

    experiment_id: str
    dataset_id: str
    experiment_agent_config_id: str  # ExperimentAgentConfig.id
    rerun_row_ids: List[str] = field(default_factory=list)  # Empty = all rows
    failed_only: bool = False  # If true, only rerun ERROR cells


@dataclass
class SetupAgentOutput:
    """Output from setting up an agent entry — columns + rows ready."""

    experiment_id: str
    experiment_agent_config_id: str
    experiment_dataset_id: str
    graph_version_id: str
    node_column_mapping: Dict[str, str]  # node_id -> column_id
    row_ids: List[str]
    status: str
    terminal_column_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class PrepareAgentRowInput:
    """Read row data + create GraphExecution record for one row."""

    row_id: str
    dataset_id: str
    experiment_id: str
    graph_version_id: str


@dataclass
class PrepareAgentRowOutput:
    """Output from preparing a row — GraphExecution created, input payload built."""

    row_id: str
    graph_execution_id: str
    input_payload: Dict[str, Any]
    status: str  # "READY" or "FAILED"
    error: Optional[str] = None


# =============================================================================
# V2 Workflow Input/Output Data Classes
# =============================================================================


@dataclass
class ProcessPromptV2WorkflowInput:
    """Input for V2 prompt workflow — resolves from ExperimentPromptConfig."""

    experiment_id: str
    dataset_id: str
    experiment_prompt_config_id: str  # ExperimentPromptConfig.id
    eval_template_ids: List[str] = field(
        default_factory=list
    )  # Dependent eval IDs (need output)
    max_concurrent_rows: int = 10
    task_queue: str = "tasks_l"
    rerun_row_ids: List[str] = field(default_factory=list)  # Empty = all rows
    failed_only: bool = False


@dataclass
class ProcessPromptV2WorkflowOutput:
    """Output from V2 prompt workflow."""

    experiment_id: str
    experiment_prompt_config_id: str
    column_id: str
    total_rows: int
    completed_rows: int
    failed_rows: int
    status: str


@dataclass
class ProcessAgentWorkflowInput:
    """Input for agent workflow — executes graph for each row."""

    experiment_id: str
    dataset_id: str
    experiment_agent_config_id: str  # ExperimentAgentConfig.id
    eval_template_ids: List[str] = field(
        default_factory=list
    )  # Dependent eval IDs (need output)
    max_concurrent_rows: int = 10
    task_queue: str = "tasks_l"
    rerun_row_ids: List[str] = field(default_factory=list)  # Empty = all rows
    failed_only: bool = False


@dataclass
class ProcessAgentWorkflowOutput:
    """Output from agent workflow."""

    experiment_id: str
    experiment_agent_config_id: str
    total_rows: int
    completed_rows: int
    failed_rows: int
    status: str


@dataclass
class AnalyzeDependenciesInput:
    """Input for analyzing eval template dependencies."""

    experiment_id: str
    eval_template_ids: List[str]


@dataclass
class AnalyzeDependenciesOutput:
    """Output from dependency analysis — splits evals into independent vs dependent."""

    independent_eval_ids: List[str]  # Evals that only reference base columns/KBs
    dependent_eval_ids: List[str]  # Evals that reference 'output' or 'prompt_chain'


@dataclass
class ResolveEdtColumnsInput:
    """Input for resolving EDT output columns (eval-only re-run)."""

    experiment_id: str
    edt_ids: List[str] = field(default_factory=list)  # Filter to specific EDTs


@dataclass
class ResolveEdtColumnsOutput:
    """EDT output column IDs + row IDs for scheduling per-EDT evals."""

    edt_column_ids: List[str]  # Output column IDs (one per EDT)
    row_ids: List[str]
    dataset_id: str


@dataclass
class RerunCellsV2WorkflowInput:
    """Input for cell/column-level rerun workflow.

    - prompt_config_ids / agent_config_ids: which configs to rerun
    - row_ids: empty = all rows; non-empty = only those rows
    - failed_only: if true, only rerun cells in ERROR status
    """

    experiment_id: str
    dataset_id: str
    prompt_config_ids: List[str] = field(default_factory=list)  # EPC IDs
    agent_config_ids: List[str] = field(default_factory=list)  # EAC IDs
    row_ids: List[str] = field(default_factory=list)  # Empty = all rows
    failed_only: bool = False
    eval_template_ids: List[str] = field(default_factory=list)
    max_concurrent_rows: int = 10
    task_queue: str = "tasks_l"
    eval_only: bool = False
    edt_ids: List[str] = field(default_factory=list)  # Filter EDT columns for eval-only
    base_eval_only: bool = False  # Skip per-EDT evals, only run base evals
    wait_for_inflight_runs: bool = False


@dataclass
class RerunCellsV2WorkflowOutput:
    """Output from cell/column-level rerun workflow."""

    experiment_id: str
    status: str
    total_rows_processed: int
    failed_rows: int
    error: Optional[str] = None


@dataclass
class StopExperimentCleanupInput:
    """Input for stop experiment cleanup activity."""

    experiment_id: str
    stop_message: str = "Execution was stopped by user"


@dataclass
class StopExperimentCleanupOutput:
    """Output from stop experiment cleanup activity."""

    experiment_id: str
    cells_cleaned: int
    status: str
    error: Optional[str] = None


@dataclass
class CreateErrorEvalCellsInput:
    """Input for creating eval cells in ERROR state when upstream prompt failed."""

    failed_row_ids: List[str]
    column_id: str  # Output/prompt column ID
    dataset_id: str
    experiment_id: str
    eval_template_ids: List[str]
    error_message: str = "Evaluation skipped: upstream prompt failed"


@dataclass
class CreateErrorEvalCellsOutput:
    """Output from creating error eval cells."""

    cells_created: int
    status: str
    error: Optional[str] = None


@dataclass
class CreateErrorAgentCellsInput:
    """Input for creating agent output cells in ERROR state when graph failed."""

    row_id: str
    dataset_id: str
    experiment_id: str
    node_column_mapping: Dict[str, str]  # node_id -> column_id
    error_message: str = "Agent execution failed"


@dataclass
class CreateErrorAgentCellsOutput:
    """Output from creating error agent cells."""

    cells_created: int
    status: str
    error: Optional[str] = None


__all__ = [
    # Activity types
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
    # V2 activity types
    "WaitForExperimentRunsInput",
    "WaitForExperimentRunsOutput",
    "SetupPromptV2Input",
    "SetupPromptV2Output",
    "SetupAgentInput",
    "SetupAgentOutput",
    "PrepareAgentRowInput",
    "PrepareAgentRowOutput",
    "ResolveEdtColumnsInput",
    "ResolveEdtColumnsOutput",
    # Workflow types
    "RunExperimentInput",
    "RunExperimentOutput",
    "ProcessPromptWorkflowInput",
    "ProcessPromptWorkflowOutput",
    # V2 workflow types
    "ProcessPromptV2WorkflowInput",
    "ProcessPromptV2WorkflowOutput",
    "ProcessAgentWorkflowInput",
    "ProcessAgentWorkflowOutput",
    "AnalyzeDependenciesInput",
    "AnalyzeDependenciesOutput",
    # Rerun cells types
    "MarkExperimentRunningInput",
    "MarkExperimentRunningOutput",
    "RerunCellsV2WorkflowInput",
    "RerunCellsV2WorkflowOutput",
    # Stop experiment cleanup types
    "StopExperimentCleanupInput",
    "StopExperimentCleanupOutput",
    # Error eval cells types
    "CreateErrorEvalCellsInput",
    "CreateErrorEvalCellsOutput",
    # Error agent cells types
    "CreateErrorAgentCellsInput",
    "CreateErrorAgentCellsOutput",
]
