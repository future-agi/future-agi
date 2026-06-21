"""Per-axis projection of simulate eval-row payloads."""

from __future__ import annotations

from typing import Any

AXIS_KEYS: tuple[str, ...] = (
    "output_pass",
    "output_score",
    "output_choices",
)

_TRACER_TO_AXIS = {
    "output_bool": "output_pass",
    "output_float": "output_score",
    "output_str_list": "output_choices",
}


def empty_axes() -> dict[str, None]:
    return dict.fromkeys(AXIS_KEYS, None)


def eval_config_output(custom_eval_config: Any) -> str:
    """Stored ``eval_template.config["output"]``; default ``"score"`` on miss."""
    try:
        return custom_eval_config.eval_template.config.get("output", "score")
    except (AttributeError, TypeError):
        return "score"


def eval_config_multi_choice(custom_eval_config: Any) -> bool:
    try:
        return bool(custom_eval_config.eval_template.multi_choice)
    except AttributeError:
        return False


def resolve_eval_axes(
    value: Any, config_output: str, multi_choice: bool = False
) -> dict[str, Any]:
    """Project ``value`` into the 3 axis keys via tracer's _dual_write_eval_value."""
    axes: dict[str, Any] = empty_axes()
    if value is None:
        return axes
    from tracer.utils.eval import _dual_write_eval_value

    projected: dict[str, Any] = {}
    _dual_write_eval_value(
        value, config_output, projected, permissive_secondary_axis=True
    )
    for tracer_key, axis_key in _TRACER_TO_AXIS.items():
        if tracer_key in projected:
            axes[axis_key] = projected[tracer_key]
    return axes


def build_simulate_eval_payload(
    *,
    value: Any,
    config_output: str,
    multi_choice: bool = False,
    reason: str = "",
    name: str = "",
    output_type: str | None = None,
    error: Any = None,
    status: str | None = None,
    skipped: bool = False,
    timestamp: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "output": value,
        **resolve_eval_axes(value, config_output, multi_choice),
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
