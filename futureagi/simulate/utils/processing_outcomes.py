from __future__ import annotations

from typing import Any, Optional

from evaluations.engine.normalize import empty_axes, resolve_eval_axes


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


def build_simulate_eval_payload(
    *,
    value: Any,
    config_output: str,
    reason: str = "",
    name: str = "",
    output_type: Optional[str] = None,
    error: Any = None,
    status: Optional[str] = None,
    skipped: bool = False,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Canonical ``CallExecution.eval_outputs`` entry shape."""
    payload: dict[str, Any] = {
        "output": value,
        **resolve_eval_axes(value, config_output),
        "reason": reason,
        "output_type": output_type,
        "name": name,
    }
    if error is not None:
        payload["error"] = error
    if status is not None:
        payload["status"] = status
    if skipped:
        payload["skipped"] = True
    if timestamp is not None:
        payload["timestamp"] = timestamp
    return payload


def build_skipped_eval_output_payload(
    *,
    eval_name: str,
    reason: Optional[str],
) -> dict:
    """Eval-output entry for a CallExecution skipped before runtime."""
    return {
        "output": None,
        **empty_axes(),
        "reason": reason,
        "output_type": None,
        "name": eval_name,
        "status": "skipped",
        "skipped": True,
    }


def pending_eval_entry() -> dict[str, Any]:
    """Placeholder ``eval_outputs[eval_id]`` written before a re-run starts."""
    return {"status": "pending", **empty_axes()}
