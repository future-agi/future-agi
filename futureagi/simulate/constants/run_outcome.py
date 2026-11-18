"""A run's verdict, as every run and summary payload reports it."""

from __future__ import annotations

from typing import Literal

OutcomeStatus = Literal["running", "cancelled", "passed", "failed", "no_score"]
OUTCOME_STATUSES: tuple[OutcomeStatus, ...] = (
    "running",
    "cancelled",
    "passed",
    "failed",
    "no_score",
)
