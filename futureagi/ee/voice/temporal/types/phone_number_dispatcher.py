"""
Data classes for PhoneNumberDispatcherWorkflow.

These types define the inputs, outputs, and state for the singleton
dispatcher workflow that manages phone number allocation for outbound calls.

Follows the same architecture as CallDispatcherWorkflow / dispatcher.py:
the dispatcher is a pure slot manager — phone DB operations are handled
by the batch activity and the calling workflow.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PhoneNumberRequest:
    """
    Request for a phone number slot from the dispatcher.

    Sent as a signal to PhoneNumberDispatcherWorkflow when an outbound call
    needs a phone number. The dispatcher will grant a slot and the batch
    activity will acquire a phone from the DB pool.
    """

    call_id: str
    org_id: str
    workflow_id: str  # CallExecutionWorkflow ID to signal when phone number is granted
    call_direction: str  # "outbound"
    requested_at: Optional[str] = None  # ISO format


@dataclass
class PhoneNumberGrantedSignal:
    """
    Signal payload sent to CallExecutionWorkflow when a phone number is acquired.

    Sent via SIGNAL_PHONE_NUMBER_GRANTED from the acquire_and_signal_phone_numbers_batch
    activity after successfully acquiring a phone number from the DB pool.
    """

    call_id: str
    phone_id: str  # SimulationPhoneNumber DB ID
    phone_number: str  # Actual phone number string
    phone_number_id: str  # Provider phone number ID


@dataclass
class PhoneNumberReleaseSignal:
    """
    Signal payload sent to PhoneNumberDispatcherWorkflow to release a phone number slot.

    Sent via SIGNAL_RELEASE_PHONE_NUMBER from CallExecutionWorkflow (via release_phone_number_slot
    activity) when a call completes, is cancelled, or fails.
    """

    call_id: str


@dataclass
class ActivePhoneNumber:
    """
    Tracking data for a phone number slot currently in use.

    Similar to ActiveCall in dispatcher.py but also tracks phone_id so the
    stale reaper can release acquired phones back to the DB pool.
    phone_id is None until the batch activity reports back successful acquisition.
    """

    call_id: str
    org_id: str
    granted_at: str  # ISO format
    phone_id: Optional[str] = (
        None  # SimulationPhoneNumber DB ID (set after acquisition)
    )


@dataclass
class PhoneNumberDispatcherState:
    """
    State for continue-as-new in PhoneNumberDispatcherWorkflow.

    Preserves the dispatcher's full context when checkpointing.
    Uses FIFO queuing with per-org limits for fairness.

    Same structure as DispatcherState in dispatcher.py.
    """

    # Pending requests in FIFO order
    pending_queue: list[PhoneNumberRequest] = field(default_factory=list)

    # Total pending count (for quick access)
    pending_count: int = 0

    # Active phone number slots: call_id -> ActivePhoneNumber
    active_phone_numbers: dict[str, ActivePhoneNumber] = field(default_factory=dict)

    # Per-org phone number counts: org_id -> count
    org_phone_number_counts: dict[str, int] = field(default_factory=dict)

    # Current limits
    app_limit: int = 100
    org_limit: int = 25

    # Lifetime counters
    total_granted: int = 0
    total_released: int = 0

    # Pending grants that have been approved but not yet signaled.
    # MUST be preserved across continue-as-new to avoid slot leaks.
    pending_grants: list[dict] = field(default_factory=list)
