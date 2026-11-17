from __future__ import annotations

from typing import Any, Optional

# Why a call the agent never answered is withheld from grading.
SILENT_AGENT_SKIP_REASON = (
    "We could not find enough agent conversation to process this call."
)


# Own key for the withheld verdict: the executor rewrites ``processing_skip_reason``
# when the platform eval job runs, but it never writes this one.
HARNESS_VERDICT_WITHHELD_KEY = "harness_verdict_withheld"


def is_silent_agent_skip(call_metadata: Any) -> bool:
    """Whether the call was withheld because the agent never answered it."""
    return (
        isinstance(call_metadata, dict)
        and call_metadata.get("processing_skipped") is True
        and call_metadata.get("processing_skip_reason") == SILENT_AGENT_SKIP_REASON
    )


def is_harness_verdict_withheld(call_metadata: Any) -> bool:
    """Whether the harness verdict of this call is withheld, whatever the receipt sealed."""
    return (
        isinstance(call_metadata, dict)
        and call_metadata.get(HARNESS_VERDICT_WITHHELD_KEY) is True
    )


def set_processing_skip_metadata(
    call_metadata: Optional[dict],
    *,
    skipped: bool,
    reason: Optional[str] = None,
) -> dict:
    """Return call_metadata updated with general processing skip state."""
    metadata = dict(call_metadata or {})
    metadata["processing_skipped"] = bool(skipped)
    metadata["processing_skip_reason"] = reason if skipped else None
    return metadata


def build_skipped_eval_output_payload(
    *,
    eval_name: str,
    reason: Optional[str],
) -> dict:
    """Build a standardized skipped eval output payload for UI rendering."""
    return {
        "output": None,
        "reason": reason,
        "output_type": None,
        "name": eval_name,
        "status": "skipped",
        "skipped": True,
    }
