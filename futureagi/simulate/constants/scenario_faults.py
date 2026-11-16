"""The ways a scenario's test condition (fault) can make the world answer a tool call; the harness's `Fault.mode`."""

FAULT_MODES = ("timeout_before_commit", "timeout_after_commit", "unavailable", "error")
