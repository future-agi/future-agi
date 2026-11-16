"""The ways a scenario's test condition (fault) can make the world answer a tool call; the harness's `Fault.mode`."""

from typing import Literal, get_args

FaultMode = Literal[
    "timeout_before_commit", "timeout_after_commit", "unavailable", "error"
]
FAULT_MODES: tuple[FaultMode, ...] = get_args(FaultMode)
