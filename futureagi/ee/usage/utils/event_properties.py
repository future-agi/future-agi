"""Helpers for usage event metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_mapping_value(data: Any, key: str) -> Any:
    if isinstance(data, Mapping):
        return data.get(key)
    return getattr(data, key, None)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def token_usage_properties(token_usage: Any) -> dict[str, int]:
    """Normalize LLM token usage into flat UsageEvent properties."""
    if not token_usage:
        return {}

    prompt_tokens = _coerce_int(
        _first_present(
            _get_mapping_value(token_usage, "prompt_tokens"),
            _get_mapping_value(token_usage, "input_tokens"),
        )
    )
    completion_tokens = _coerce_int(
        _first_present(
            _get_mapping_value(token_usage, "completion_tokens"),
            _get_mapping_value(token_usage, "output_tokens"),
        )
    )
    total_tokens = _coerce_int(_get_mapping_value(token_usage, "total_tokens"))

    if total_tokens is None and (
        prompt_tokens is not None or completion_tokens is not None
    ):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    properties: dict[str, int] = {}
    if prompt_tokens is not None:
        properties["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        properties["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        properties["total_tokens"] = total_tokens
    return properties


def llm_usage_properties(obj: Any) -> dict[str, int]:
    """Extract token usage from an object or its nested LLM, when available."""
    if obj is None:
        return {}

    direct = token_usage_properties(getattr(obj, "token_usage", None))
    if direct:
        return direct

    nested_llm = getattr(obj, "llm", None)
    return token_usage_properties(getattr(nested_llm, "token_usage", None))
