"""Dataclasses shared by the workflow + activities + client.

Kept Django-free so the workflow sandbox can import them.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RecalculateTarget:
    """One sibling EvalLogger to rerun. Exactly one of the three id fields is set."""

    target_type: str  # EvalTargetType value: "span" / "trace" / "session"
    observation_span_id: Optional[str] = None
    trace_id: Optional[str] = None
    trace_session_id: Optional[str] = None


@dataclass
class SoftDeleteSiblingsInput:
    eval_task_id: str
    custom_eval_config_id: str


@dataclass
class SoftDeleteSiblingsOutput:
    deleted_count: int


@dataclass
class DispatchRerunInput:
    target_type: str
    custom_eval_config_id: str
    eval_task_id: Optional[str] = None
    feedback_id: Optional[str] = None
    observation_span_id: Optional[str] = None
    trace_id: Optional[str] = None
    trace_session_id: Optional[str] = None


@dataclass
class DispatchRerunOutput:
    target_type: str
    status: str  # "completed" / "failed"
    error: Optional[str] = None


@dataclass
class RecalculateEvalTaskWorkflowInput:
    eval_task_id: str
    custom_eval_config_id: str
    feedback_id: Optional[str] = None
    targets: List[RecalculateTarget] = field(default_factory=list)
    max_concurrent: int = 10
    task_queue: str = "tasks_s"


@dataclass
class RecalculateEvalTaskWorkflowOutput:
    total: int
    completed: int
    failed: int
    status: str  # "COMPLETED" / "PARTIAL" / "FAILED"


__all__ = [
    "RecalculateTarget",
    "SoftDeleteSiblingsInput",
    "SoftDeleteSiblingsOutput",
    "DispatchRerunInput",
    "DispatchRerunOutput",
    "RecalculateEvalTaskWorkflowInput",
    "RecalculateEvalTaskWorkflowOutput",
]
