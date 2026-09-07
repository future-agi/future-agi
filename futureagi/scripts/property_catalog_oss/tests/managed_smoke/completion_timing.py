"""Fixture-only correctness wait; slow completion is not a latency-target pass."""

import math

COMPLETION_TIMEOUT_SECONDS = 180


def completion_timing(elapsed_seconds, *, latency_target_seconds):
    if (
        not math.isfinite(elapsed_seconds)
        or not 0 <= elapsed_seconds <= COMPLETION_TIMEOUT_SECONDS
    ):
        raise RuntimeError("automatic completion exceeded the 180s correctness wait")
    if not 0 < latency_target_seconds <= COMPLETION_TIMEOUT_SECONDS:
        raise ValueError("invalid latency target")
    return {
        "completion_seconds": elapsed_seconds,
        "correctness_deadline_seconds": COMPLETION_TIMEOUT_SECONDS,
        "latency_target_seconds": latency_target_seconds,
        "latency_target_met": elapsed_seconds <= latency_target_seconds,
    }
