"""
Data classes for CallDispatcherWorkflow.

These types define the inputs, outputs, and state for the singleton
dispatcher workflow that manages call slot allocation and rate limiting.
"""

from dataclasses import dataclass, field


@dataclass
class SlotRequest:
    """
    Request for a call slot from the dispatcher.

    Sent as a signal to CallDispatcherWorkflow when a call is ready to execute.
    The dispatcher will grant the slot when capacity is available.
    """

    # Call identifier
    call_id: str

    # Organization ID (for per-org rate limiting)
    org_id: str

    # CallExecutionWorkflow ID to signal when slot is granted
    workflow_id: str

    # Timestamp for FIFO ordering (optional, set by dispatcher if not provided)
    requested_at: str | None = None  # ISO format

    # Agent-level concurrency (for LiveKit agents)
    agent_definition_id: str | None = None
    agent_concurrency_limit: int | None = None


@dataclass
class SlotGrant:
    """
    Grant notification for a call slot.

    Used internally by the dispatcher to batch signal slot grants.
    """

    # Workflow to notify
    workflow_id: str

    # Call that was granted the slot
    call_id: str


@dataclass
class ActiveCall:
    """
    Tracking data for an active call holding a slot.

    Stored by the dispatcher to track which calls are currently executing.
    """

    # Call identifier
    call_id: str

    # Organization ID
    org_id: str

    # When the slot was granted (ISO format)
    granted_at: str

    # Agent definition ID (for per-agent concurrency tracking)
    agent_definition_id: str | None = None


@dataclass
class DispatcherStatus:
    """
    Query response for CallDispatcherWorkflow status.

    Provides visibility into the current state of call slot allocation.
    """

    # Queue status
    pending_count: int
    active_count: int

    # Per-organization breakdown
    org_counts: dict[str, int] = field(default_factory=dict)

    # Current limits
    app_limit: int = 100
    org_limit: int = 25

    # Lifetime counters
    total_granted: int = 0
    total_released: int = 0

    # Additional metrics
    orgs_with_pending: int = 0


@dataclass
class DispatcherState:
    """
    State for continue-as-new in CallDispatcherWorkflow.

    This state preserves the dispatcher's full context when checkpointing.
    Uses FIFO queuing with per-org limits for fairness and simplicity.
    """

    # Pending requests in FIFO order (first come, first served)
    # Requests are granted if: app_limit allows AND org_limit allows
    pending_queue: list[SlotRequest] = field(default_factory=list)

    # Total pending count (for quick access)
    pending_count: int = 0

    # Active calls tracking: call_id -> ActiveCall
    active_calls: dict[str, ActiveCall] = field(default_factory=dict)

    # Per-org call counts: org_id -> count (for enforcing org_limit)
    org_call_counts: dict[str, int] = field(default_factory=dict)

    # Per-agent call counts: agent_definition_id -> count (for LiveKit concurrency)
    agent_call_counts: dict[str, int] = field(default_factory=dict)

    # Per-agent concurrency limits: agent_definition_id -> max_concurrent
    agent_limits: dict[str, int] = field(default_factory=dict)

    # Current limits
    app_limit: int = 100  # Max concurrent calls across all orgs
    org_limit: int = 25  # Max concurrent calls per org

    # Lifetime counters
    total_granted: int = 0
    total_released: int = 0

    # Pending grants that have been added to active_calls but not yet signaled.
    # This MUST be preserved across continue-as-new to avoid slot leaks.
    # Without this, granted slots would stay in active_calls but workflows
    # would never receive the SLOT_GRANTED signal (waiting forever).
    pending_grants: list[dict] = field(default_factory=list)
