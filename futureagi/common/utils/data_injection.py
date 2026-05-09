"""Helpers for normalizing eval `data_injection` config across surfaces.

The frontend has settled on snake_case keys (full_row, span_context,
trace_context, session_context, call_context, variables_only) but older
saved configs and a few code paths used camelCase (fullRow, spanContext,
...) or the alias `dataset_row` for full_row. This module is the single
place that bridges those.
"""

from typing import Any, Dict, Mapping, Optional

CANONICAL_FLAGS = (
    "full_row",
    "span_context",
    "trace_context",
    "session_context",
    "call_context",
    "variables_only",
)

# snake_case canonical name → set of legacy aliases.
_ALIASES: Dict[str, tuple] = {
    "full_row": ("fullRow", "dataset_row", "datasetRow"),
    "span_context": ("spanContext",),
    "trace_context": ("traceContext",),
    "session_context": ("sessionContext",),
    "call_context": ("callContext",),
    "variables_only": ("variablesOnly",),
}


def normalize(data_injection: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    """Return a dict keyed by canonical snake_case flag names with bool values.

    Unknown keys are dropped. Missing flags default to False.
    """
    if not isinstance(data_injection, Mapping):
        return {flag: False for flag in CANONICAL_FLAGS}
    out = {}
    for flag in CANONICAL_FLAGS:
        value = data_injection.get(flag)
        if not value:
            for alias in _ALIASES.get(flag, ()):
                if data_injection.get(alias):
                    value = data_injection[alias]
                    break
        out[flag] = bool(value)
    return out


def is_enabled(data_injection: Optional[Mapping[str, Any]], flag: str) -> bool:
    """Return True if the given canonical flag is enabled in data_injection."""
    if flag not in CANONICAL_FLAGS:
        raise ValueError(f"Unknown data_injection flag: {flag!r}")
    return normalize(data_injection).get(flag, False)
