from __future__ import annotations

from typing import Optional

from evaluations.engine.normalize import empty_axes


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
    """Build a standardized skipped eval output payload for UI rendering.

    Mirrors the canonical shape produced by
    ``evaluations.engine.normalize.build_simulate_eval_payload`` so every row
    in ``CallExecution.eval_outputs`` carries the same key set regardless of
    status.
    """
    return {
        "output": None,
        **empty_axes(),
        "reason": reason,
        "output_type": None,
        "name": eval_name,
        "status": "skipped",
        "skipped": True,
    }
