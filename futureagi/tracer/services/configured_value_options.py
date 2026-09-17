"""Canonical finite-value handling for configured filter options."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def configured_value_options(options: object) -> tuple[dict[str, Any], ...]:
    """Normalize and de-duplicate finite configured options.

    Values remain JSON-typed so filters preserve booleans, numbers, and
    structured choices instead of coercing everything to display text.
    """

    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return ()

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        raw_value = option
        raw_label = option
        if isinstance(option, Mapping):
            raw_value = option.get("value")
            if raw_value in (None, ""):
                raw_value = option.get("label")
            if raw_value in (None, ""):
                raw_value = option.get("name")

            raw_label = option.get("label")
            if raw_label in (None, ""):
                raw_label = option.get("name")
            if raw_label in (None, ""):
                raw_label = raw_value

        if raw_value in (None, ""):
            continue
        try:
            serialized = json.dumps(
                raw_value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            continue
        identity = (type(raw_value).__name__, serialized)
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append({"value": raw_value, "label": str(raw_label)})
    return tuple(normalized)
