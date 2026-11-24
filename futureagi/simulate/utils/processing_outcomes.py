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
        "reason": reason,
        "output_type": None,
        "name": eval_name,
        "status": "skipped",
        "skipped": True,
    }


# Fixed reason code for an eval the shared evaluator refused because one of
# its inputs was empty on the call. Readers count it apart from errors.
REASON_INPUT_MISSING = "input_missing"


def build_missing_input_eval_output_payload(
    *,
    eval_name: str,
    reason: str | None,
    fields: tuple[str, ...] | list[str],
    output_type: str | None,
) -> dict:
    """A skipped eval whose input was empty: not graded, and not an error."""
    from django.utils import timezone

    payload = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    payload.update(
        {
            "reason_code": REASON_INPUT_MISSING,
            "missing_fields": list(fields),
            "output_type": output_type,
            "timestamp": timezone.now().isoformat(),
        }
    )
    return payload


def record_missing_eval_input(call_execution, eval_config, error) -> dict:
    """Store an eval refused for an empty input on the call, and return it.

    The eval itself is fine, so its config's status is left as it is.
    """
    from simulate.utils.eval_summary import derive_kpi_output_type

    payload = build_missing_input_eval_output_payload(
        eval_name=eval_config.name,
        reason=str(error),
        fields=getattr(error, "fields", ()),
        output_type=derive_kpi_output_type(eval_config.eval_template),
    )
    if not call_execution.eval_outputs:
        call_execution.eval_outputs = {}
    call_execution.eval_outputs[str(eval_config.id)] = payload
    call_execution.save(update_fields=["eval_outputs"])
    return payload
