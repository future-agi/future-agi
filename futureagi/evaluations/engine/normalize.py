"""Per-axis projection helpers for eval-row payloads."""

from __future__ import annotations

import ast
import json
from typing import Any, NotRequired, TypedDict

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


class EvalAxes(TypedDict):
    output_bool: bool | None
    output_float: float | None
    output_str_list: list[str] | None
    output_str: NotRequired[str | None]


class EvalAxesApi(TypedDict):
    output_pass: bool | None
    output_score: float | None
    output_choices: list[str] | None


def empty_axes() -> dict[str, None]:
    return dict.fromkeys(AXIS_KEYS, None)


def eval_config_output(custom_eval_config: Any) -> str:
    """Stored ``eval_template.config["output"]``; default ``"score"`` on miss."""
    try:
        return custom_eval_config.eval_template.config.get("output", "score")
    except (AttributeError, TypeError):
        return "score"


def resolve_eval_axes(
    value: Any, config_output: str, *, include_output_str: bool = False
) -> EvalAxes:
    """Project ``value`` into typed columns; missing keys default to None."""
    keys = AXIS_KEYS + (("output_str",) if include_output_str else ())
    axes: dict[str, Any] = dict.fromkeys(keys, None)
    if value is None:
        return axes  # type: ignore[return-value]
    from tracer.utils.eval import _dual_write_eval_value

    projected: dict[str, Any] = {}
    _dual_write_eval_value(
        value, config_output, projected, permissive_secondary_axis=True
    )
    for key in keys:
        if key in projected:
            axes[key] = projected[key]
    return axes  # type: ignore[return-value]


def project_storage_axes_to_api(eval_data: dict) -> EvalAxesApi:
    """Read storage-axis keys off ``eval_data``, return API-named dict."""
    return {api: eval_data.get(storage) for storage, api in AXIS_STORAGE_TO_API}  # type: ignore[return-value]


def rename_value_infos_axes(raw: Any) -> Any:
    """Decode JSON-string ``value_infos`` and rename storage axes to API keys."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return raw
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    for storage_key, api_key in AXIS_STORAGE_TO_API:
        if storage_key in out:
            out[api_key] = out.pop(storage_key)
    return out


def parse_legacy_value(raw: Any) -> Any:
    """Decode legacy string-encoded eval values; pass non-strings through."""
    if raw is None or not isinstance(raw, str):
        return raw
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, RecursionError, MemoryError, TypeError):
        return raw
