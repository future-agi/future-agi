"""
Data classes for CallExecutionWorkflow.

These types define the inputs, outputs, and intermediate data for
individual call execution workflows.
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CallExecutionInput:
    """
    Input to CallExecutionWorkflow.

    This is minimal for scalability - full call details are fetched
    from the database by the prepare_call activity.

    With 1000+ calls, keeping this minimal prevents memory bloat
    in the parent workflow when launching child workflows.
    """

    # Call identifier (used to fetch full data from DB)
    call_id: str

    # Organization ID (for rate limiting, balance checks)
    org_id: str

    # Workspace ID (for multi-tenancy scoping)
    # Agent definitions, scenarios, simulator agents can have same names
    # across different workspaces within the same organization
    workspace_id: str

    # Parent workflow ID (for signaling completion)
    test_workflow_id: str

    # Test execution ID (for evaluation context)
    test_execution_id: str

    # If True, skip call execution and only run evaluations on existing data
    # Used for eval-only reruns where the call data already exists
    eval_only: bool = False

    # Billing event type for usage gating: "voice_call" or "text_call"
    event_type: str = "voice_call"


@dataclass
class CallInfo:
    """
    Scenario-specific call data.

    Returned by the prepare_call activity after fetching from database.
    Contains all the information needed to configure and execute the call.
    """

    # Identifiers
    call_id: str
    scenario_id: str

    # Scenario data for dynamic prompt generation
    row_data: dict[str, Any]

    # Call direction
    is_outbound: bool


@dataclass
class CallResult:
    """
    Basic call outcome from the provider.

    This is an intermediate result returned by monitor_call_until_complete,
    before evaluations are run. It represents the raw outcome from the
    call provider (VAPI, etc.).
    """

    # Call status from provider
    status: str  # "ended", "failed", "cancelled", "no-answer", etc.

    # Duration in seconds (None if call didn't connect)
    duration_seconds: Optional[int] = None

    # Reason for call ending (provider-specific)
    ended_reason: Optional[str] = None

    # Error message (if failed)
    error: Optional[str] = None


@dataclass
class CallExecutionOutput:
    """
    Complete output from CallExecutionWorkflow.

    This is the final return value of the workflow. Note that for scalability,
    the parent workflow does NOT wait for this output - instead it receives
    a minimal completion signal. Full results are stored in the database.
    """

    # Final status
    status: str  # COMPLETED, FAILED, CANCELLED, REQUEUE

    # Call identifier
    call_id: str

    # Provider-assigned call ID (for debugging/lookup)
    provider_call_id: Optional[str] = None

    # Call duration
    duration_seconds: Optional[int] = None

    # Error information (if failed)
    error: Optional[str] = None


@dataclass
class CallStatus:
    """
    Query response for CallExecutionWorkflow status.

    Returned by the get_status query to check on individual call progress.
    """

    # Current workflow phase
    status: str  # PENDING, PREPARING, ACQUIRING, CALLING, MONITORING, STORING, EVALUATING, DOWNSTREAM_TASKS, CLEANUP, COMPLETED, FAILED, CANCELLED

    # Provider call ID (available after call creation)
    provider_call_id: Optional[str] = None

    # Current phase details
    phase_details: Optional[str] = None
