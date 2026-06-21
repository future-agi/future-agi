"""Per-axis projection helpers for eval-row payloads."""

from __future__ import annotations

import ast
from typing import Any

AXIS_KEYS: tuple[str, ...] = (
    "output_bool",
    "output_float",
    "output_str_list",
)

AXIS_STORAGE_TO_API: tuple[tuple[str, str], ...] = (
    ("output_bool", "output_pass"),
    ("output_float", "output_score"),
    ("output_str_list", "output_choices"),
)


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


def project_eval_value(
    value: Any, config_output: str, *, include_output_str: bool = False
) -> dict[str, Any]:
    """Project ``value`` to typed columns via tracer's ``_dual_write_eval_value``.

    Returns only the keys populated by the helper. ``include_output_str=False``
    (the default) drops ``output_str`` so callers writing to surfaces that
    don't carry that column don't need to filter it out themselves.
    """
    if value is None:
        return {}
    from tracer.utils.eval import _dual_write_eval_value

    projected: dict[str, Any] = {}
    _dual_write_eval_value(
        value, config_output, projected, permissive_secondary_axis=True
    )
    if not include_output_str:
        projected.pop("output_str", None)
    return projected


def resolve_eval_axes(
    value: Any, config_output: str, multi_choice: bool = False
) -> dict[str, Any]:
    """Project ``value`` into the 3 axis keys; missing axes default to None."""
    projected = project_eval_value(value, config_output)
    axes = empty_axes()
    for key in AXIS_KEYS:
        if key in projected:
            axes[key] = projected[key]
    return axes


def project_storage_axes_to_api(eval_data: dict) -> dict[str, Any]:
    """Read storage-axis keys off ``eval_data``, return API-named dict."""
    return {api: eval_data.get(storage) for storage, api in AXIS_STORAGE_TO_API}


def parse_legacy_value(raw: Any) -> Any:
    """Decode legacy string-encoded eval values; pass non-strings through."""
    if raw is None or not isinstance(raw, str):
        return raw
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, RecursionError, MemoryError, TypeError):
        return raw
