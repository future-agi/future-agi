"""Dataclasses shared by the eval-task activities and workflows.

Kept free of Django imports so ``workflows.py`` can import them inside the
Temporal sandbox.
"""

from dataclasses import dataclass

# How a workflow run ended (distinct from the EvalTask DB status enum).
WF_STATUS_COMPLETED = "completed"
WF_STATUS_PAUSED = "paused"


@dataclass
class EvalTaskWorkflowInput:
    """Input for the historical workflow (and its continue-as-new hops)."""

    task_id: str
    task_queue: str = "tasks_s"
    batch_size: int = 50
    max_concurrent: int = 10
    # Set on continue-as-new so the next run skips the one-time reconcile + reap.
    already_reconciled: bool = False
    # Carried across continue-as-new so the final output reports the lifetime
    # total drained, not just the last segment.
    processed: int = 0
    # Test/ops override; production relies on the server's CAN suggestion.
    continue_as_new_after_batches: int | None = None


@dataclass
class EvalTaskWorkflowOutput:
    task_id: str
    status: str
    processed: int


@dataclass
class ContinuousDrainState:
    """Input + continue-as-new checkpoint for the continuous workflow."""

    task_id: str
    task_queue: str = "tasks_s"
    batch_size: int = 50
    max_concurrent: int = 10
    poll_interval_seconds: int = 30
    processed: int = 0
    batches: int = 0
    continue_as_new_after_batches: int | None = None


@dataclass
class ReconcileActivityInput:
    task_id: str


@dataclass
class ReconcileActivityOutput:
    task_id: str
    created: int
    requeued: int
    dropped: int


@dataclass
class ClaimBatchInput:
    task_id: str
    n: int


@dataclass
class ClaimBatchOutput:
    entry_ids: list[str]


@dataclass
class RunEntryInput:
    entry_id: str


@dataclass
class RunEntryOutput:
    entry_id: str
    status: str


@dataclass
class ReapInput:
    task_id: str
    older_than_seconds: int = 600
    max_attempts: int = 3


@dataclass
class ReapOutput:
    requeued: int
    failed: int


@dataclass
class TaskStateInput:
    task_id: str


@dataclass
class TaskStateOutput:
    task_id: str
    status: str
    active: bool
    has_undrained_work: bool


@dataclass
class RequeueEntriesInput:
    task_id: str
    entry_ids: list[str]


@dataclass
class RequeueEntriesOutput:
    requeued: int


@dataclass
class SetStatusInput:
    task_id: str
    status: str
    # Optional atomic guard: only change the row if it is currently this status
    # (so a concurrent pause/delete is never clobbered). None = unconditional.
    expected_status: str | None = None


@dataclass
class SetStatusOutput:
    task_id: str
    changed: bool
    status: str


@dataclass
class FinalizeInput:
    task_id: str


@dataclass
class FinalizeOutput:
    task_id: str
    finalized: bool
    status: str


@dataclass
class WorkflowLabelsInput:
    task_id: str


@dataclass
class WorkflowLabelsOutput:
    """Values for the workflow's Search Attributes (org/project/run_type) and
    memo (the display-only context)."""

    org_id: str
    project_id: str
    run_type: str
    task_name: str
    project_name: str
    org_name: str
    config_summary: str


__all__ = [
    "WF_STATUS_COMPLETED",
    "WF_STATUS_PAUSED",
    "EvalTaskWorkflowInput",
    "EvalTaskWorkflowOutput",
    "ContinuousDrainState",
    "ReconcileActivityInput",
    "ReconcileActivityOutput",
    "ClaimBatchInput",
    "ClaimBatchOutput",
    "RunEntryInput",
    "RunEntryOutput",
    "ReapInput",
    "ReapOutput",
    "TaskStateInput",
    "TaskStateOutput",
    "RequeueEntriesInput",
    "RequeueEntriesOutput",
    "SetStatusInput",
    "SetStatusOutput",
    "FinalizeInput",
    "FinalizeOutput",
    "WorkflowLabelsInput",
    "WorkflowLabelsOutput",
]
