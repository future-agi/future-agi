from __future__ import annotations

from typing import Optional


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
        "output_scalar": None,
        "output_dict": None,
        "reason": reason,
        "output_type": None,
        "name": eval_name,
        "status": "skipped",
        "skipped": True,
    }
