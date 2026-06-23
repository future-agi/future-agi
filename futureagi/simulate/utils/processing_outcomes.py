from __future__ import annotations


def set_processing_skip_metadata(
    call_metadata: dict | None,
    *,
    skipped: bool,
    reason: str | None = None,
) -> dict:
    """Return call_metadata updated with general processing skip state."""
    metadata = dict(call_metadata or {})
    metadata["processing_skipped"] = bool(skipped)
    metadata["processing_skip_reason"] = reason if skipped else None
    return metadata


def build_skipped_eval_output_payload(
    *,
    eval_name: str,
    reason: str | None,
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
