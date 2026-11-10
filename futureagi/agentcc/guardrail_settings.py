"""Canonical tenant pipeline settings shared by writes and gateway pushes."""

from pydantic import TypeAdapter

_BOOLEAN = TypeAdapter(bool)
_ALIASES = ("fail_open", "failOpen", "pipeline_mode", "pipelineMode")


def pipeline_settings(value):
    """Require tenant opt-in; never use Python truthiness on wire values.

    Historical camelCase names are accepted. Conflicting aliases are refused
    instead of making policy depend on which consumer reads the document.
    """
    flags = [
        _BOOLEAN.validate_python(value[key])
        for key in ("fail_open", "failOpen")
        if key in value and value[key] is not None
    ]
    if len(set(flags)) > 1:
        raise ValueError("conflicting fail_open aliases")
    modes = [
        str(value[key]).strip().lower()
        for key in ("pipeline_mode", "pipelineMode")
        if value.get(key) is not None
    ]
    if any(mode not in ("parallel", "sequential") for mode in modes):
        raise ValueError("pipeline_mode must be parallel or sequential")
    if len(set(modes)) > 1:
        raise ValueError("conflicting pipeline_mode aliases")
    return {
        "fail_open": flags[0] if flags else False,
        "pipeline_mode": modes[0] if modes else "parallel",
    }


def normalize_guardrail_settings(value):
    """Preserve check definitions and return one canonical setting per policy."""
    return {
        **{key: item for key, item in value.items() if key not in _ALIASES},
        **pipeline_settings(value),
    }
