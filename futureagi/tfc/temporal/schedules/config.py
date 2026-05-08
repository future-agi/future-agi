"""
Schedule configuration dataclass.

This module provides the ScheduleConfig dataclass used to define
Temporal schedules across all domains (model_hub, tracer, simulate, etc.).
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from temporalio.client import ScheduleOverlapPolicy


@dataclass
class ScheduleConfig:
    """Configuration for a Temporal schedule.

    Set exactly one of ``interval_seconds`` (epoch-aligned interval firing)
    or ``cron_expression`` (calendar-aligned firing). For schedules that
    fire less than once per Temporal's default catchup window (~1 min),
    set ``catchup_window_seconds`` so a missed firing is retried when the
    worker comes back instead of being silently dropped.

    Set ``workflow_class`` to start a dedicated workflow class instead of
    the generic ``TaskRunnerWorkflow``. Use this when the workflow needs
    deterministic state at fire time (e.g. a closing period derived from
    ``workflow.now()``) that the activity cannot reconstruct reliably
    from its own wall clock.
    """

    schedule_id: str
    activity_name: str
    interval_seconds: int = 0
    cron_expression: Optional[str] = None
    catchup_window_seconds: int = 0
    queue: str = "default"
    description: Optional[str] = None
    overlap_policy: ScheduleOverlapPolicy = field(default=ScheduleOverlapPolicy.SKIP)
    workflow_class: Optional[Any] = None
    schedule_to_start_timeout_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.cron_expression and self.interval_seconds <= 0:
            raise ValueError(
                f"ScheduleConfig {self.schedule_id!r} must set either "
                f"interval_seconds (>0) or cron_expression."
            )

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self.interval_seconds)

    @property
    def catchup_window(self) -> Optional[timedelta]:
        if self.catchup_window_seconds <= 0:
            return None
        return timedelta(seconds=self.catchup_window_seconds)


__all__ = ["ScheduleConfig"]
