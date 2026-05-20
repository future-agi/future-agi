"""
Data classes for RerunCoordinatorWorkflow.

These types define the inputs, outputs, and state for the rerun coordinator
workflow that manages rerunning specific call executions.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RerunCoordinatorInput:
    """
    Input to RerunCoordinatorWorkflow.

    This is passed when starting a rerun execution. If `state` is provided,
    the workflow is being resumed from a continue-as-new checkpoint.
    """

    # Parent test execution being rerun
    test_execution_id: str

    # Call execution IDs to rerun
    call_execution_ids: list[str]

    # Organization and workspace for child workflows
    org_id: str
    workspace_id: str

    # Unique run identifier (timestamp-based) for workflow ID uniqueness
    rerun_id: str

    # If True, only run evaluations on existing call data (no call execution)
    # If False, run full call + evaluation flow
    eval_only: bool = False

    # None means run every eval config attached to the run_test.
    eval_config_ids: Optional[list[str]] = None

    # State for continue-as-new (None for initial run)
    state: Optional["RerunCoordinatorState"] = None

    # All call IDs seen so far (for continue-as-new, preserving cancellation list)
    all_call_ids: Optional[list[str]] = None


@dataclass
class RerunCoordinatorState:
    """
    Lightweight state for continue-as-new in RerunCoordinatorWorkflow.

    This state allows the workflow to checkpoint progress without storing
    full call results in memory. Results are stored in the database.

    Used when workflow history exceeds CONTINUE_AS_NEW_THRESHOLD events.
    """

    total_calls: int
    completed_calls: int
    failed_calls: int
    launched_calls: int

    # All call IDs that have been seen (launched + pending), for cancellation.
    # Preserved across continue-as-new so we can cancel already-launched children.
    all_call_ids: list[str] = field(default_factory=list)


@dataclass
class RerunCoordinatorOutput:
    """
    Output from RerunCoordinatorWorkflow.

    Returned when the workflow completes (successfully or with failure).
    """

    status: str  # COMPLETED, FAILED, CANCELLED

    # Counters
    total_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0

    # Error information (if failed)
    error: Optional[str] = None


@dataclass
class RerunCoordinatorStatus:
    """
    Query response for RerunCoordinatorWorkflow status.

    Returned by the get_status query to provide real-time execution progress.
    """

    status: str  # PENDING, LAUNCHING, RUNNING, FINALIZING, COMPLETED, FAILED, CANCELLED

    # Progress counters
    total_calls: int
    completed_calls: int
    failed_calls: int
    launched_calls: int = 0


# =============================================================================
# Signal Types
# =============================================================================


@dataclass
class MergeCallsSignal:
    """
    Signal to merge additional call IDs into a running RerunCoordinatorWorkflow.

    This enables the "single active rerun" pattern where new rerun requests
    are merged into the existing workflow instead of starting a new one.
    """

    # Additional call execution IDs to rerun
    call_execution_ids: list[str]

    # Whether these calls are eval-only (must match workflow's eval_only mode)
    eval_only: bool = False

    # None means run every eval config attached to the run_test.
    eval_config_ids: Optional[list[str]] = None


# =============================================================================
# Activity Input/Output Types
# =============================================================================


@dataclass
class FinalizeRerunInput:
    """
    Input for finalize_rerun_execution activity.

    Activity: Recalculates TestExecution counts from CallExecution statuses
    and updates the final status. Also clears active rerun tracking.
    """

    test_execution_id: str

    # Clear active rerun tracking when finalizing
    clear_active_rerun: bool = True


@dataclass
class CancelRerunCallsInput:
    """
    Input for cancel_rerun_calls activity.

    Activity: Cancels all rerun child workflows and updates CallExecution
    statuses to CANCELLED.
    """

    test_execution_id: str
    call_execution_ids: list[str]
    rerun_id: str
    reason: str = "Cancelled by user"
