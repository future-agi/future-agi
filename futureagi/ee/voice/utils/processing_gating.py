from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from simulate.temporal.constants import MIN_DURATION_SECONDS_WITHOUT_TRANSCRIPT


@dataclass(frozen=True)
class ProcessingSkipDecision:
    processing_skipped: bool
    processing_skip_reason: str


def decide_processing_skip(
    *,
    message_count: int,
    has_agent_message: bool,
    has_customer_message: bool,
    duration_seconds: Optional[float],
) -> ProcessingSkipDecision:
    """Determine whether CSAT/evaluations should be skipped."""
    if message_count > 0:
        if has_agent_message and has_customer_message:
            return ProcessingSkipDecision(
                processing_skipped=False,
                processing_skip_reason="",
            )

        if not has_agent_message and not has_customer_message:
            return ProcessingSkipDecision(
                processing_skipped=True,
                processing_skip_reason=(
                    "We could not find enough conversation from either side to process this call."
                ),
            )

        if not has_agent_message:
            return ProcessingSkipDecision(
                processing_skipped=True,
                processing_skip_reason=(
                    "We could not find enough agent conversation to process this call."
                ),
            )

        return ProcessingSkipDecision(
            processing_skipped=True,
            processing_skip_reason=(
                "We could not find enough customer conversation to process this call."
            ),
        )

    threshold = float(MIN_DURATION_SECONDS_WITHOUT_TRANSCRIPT)
    if duration_seconds is not None and float(duration_seconds) > threshold:
        return ProcessingSkipDecision(
            processing_skipped=False,
            processing_skip_reason="",
        )

    if duration_seconds is None:
        return ProcessingSkipDecision(
            processing_skipped=True,
            processing_skip_reason=(
                "Call transcript is unavailable and we could not determine call duration, "
                "so processing was skipped."
            ),
        )

    return ProcessingSkipDecision(
        processing_skipped=True,
        processing_skip_reason=(
            f"Call transcript is unavailable and the call was too short ({float(duration_seconds):.1f}s) "
            f"to process (minimum {int(threshold)}s)."
        ),
    )
