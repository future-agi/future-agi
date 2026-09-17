"""Shared watermark policy for continuous evaluation-task reconciliation."""

from datetime import timedelta

# Re-read this tail after every successful pass so late ClickHouse arrivals and
# version changes remain visible. A catch-up slice must be wider than the tail;
# otherwise parking its cursor would make no forward progress.
CONTINUOUS_CURSOR_OVERLAP = timedelta(minutes=5)
CONTINUOUS_MIN_CURSOR_ADVANCE = timedelta(seconds=1)
CONTINUOUS_MIN_PROOF_WINDOW = CONTINUOUS_CURSOR_OVERLAP + CONTINUOUS_MIN_CURSOR_ADVANCE


__all__ = [
    "CONTINUOUS_CURSOR_OVERLAP",
    "CONTINUOUS_MIN_CURSOR_ADVANCE",
    "CONTINUOUS_MIN_PROOF_WINDOW",
]
