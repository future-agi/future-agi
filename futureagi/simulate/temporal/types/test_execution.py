"""
Data classes for TestExecutionWorkflow.

These types define the inputs, outputs, and state for the parent orchestrator
workflow that manages the complete test execution lifecycle.
"""

from dataclasses import dataclass, field


@dataclass
class TestExecutionState:
    """
    Lightweight state for continue-as-new in TestExecutionWorkflow.

    This state allows the workflow to checkpoint progress without storing
    full call results in memory. Results are stored in the database.

    Used when workflow history exceeds CONTINUE_AS_NEW_THRESHOLD events.
    """

    status: str
    total_calls: int
    completed_calls: int
    failed_calls: int
    launched_calls: int
    analyzing_calls: int = 0  # Calls that have entered ANALYZING state


@dataclass
class TestExecutionInput:
    """
    Input to TestExecutionWorkflow.

    This is passed when starting a new test execution. If `state` is provided,
    the workflow is being resumed from a continue-as-new checkpoint.
    """

    # Required identifiers
    test_execution_id: str
    run_test_id: str
    org_id: str

    # Scenario configuration
    scenario_ids: list[str] = field(default_factory=list)

    # Optional simulator agent
    simulator_id: str | None = None

    # State for continue-as-new (None for initial run)
    state: TestExecutionState | None = None


@dataclass
class TestExecutionOutput:
    """
    Output from TestExecutionWorkflow.

    Returned when the workflow completes (successfully or with failure).
    """

    status: str  # COMPLETED, FAILED, CANCELLED

    # Counters
    total_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0

    # Error information (if failed)
    error: str | None = None


@dataclass
class TestExecutionStatus:
    """
    Query response for TestExecutionWorkflow status.

    Returned by the get_status query to provide real-time execution progress.
    """

    status: str  # PENDING, INITIALIZING, LAUNCHING, RUNNING, EVALUATING, FINALIZING, COMPLETED, FAILED, CANCELLING, CANCELLED

    # Progress counters
    total_calls: int
    completed_calls: int
    failed_calls: int
    launched_calls: int = 0
    analyzing_calls: int = 0  # Calls that have entered ANALYZING state

    # Optional timing info
    started_at: str | None = None  # ISO format timestamp
    elapsed_seconds: int | None = None


@dataclass
class CallCompletedSignal:
    """
    Signal payload for call completion notification.

    Sent from CallExecutionWorkflow to TestExecutionWorkflow when a call
    completes (successfully, fails, or is cancelled).
    """

    call_id: str
    status: str
    failed: bool


@dataclass
class CallAnalyzingSignal:
    """
    Signal payload for call analyzing notification.

    Sent from CallExecutionWorkflow to TestExecutionWorkflow when a call
    enters the ANALYZING state (call completed, now processing results/evals).
    This allows the parent to transition to EVALUATING status earlier.
    """

    call_id: str
