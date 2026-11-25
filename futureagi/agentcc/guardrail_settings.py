"""Canonical tenant pipeline settings shared by writes and gateway pushes."""

from pydantic import TypeAdapter

_BOOLEAN = TypeAdapter(bool)
_FAIL_OPEN_KEYS = ("fail_open", "failOpen")
_PIPELINE_MODE_KEYS = ("pipeline_mode", "pipelineMode")
_ALIASES = _FAIL_OPEN_KEYS + _PIPELINE_MODE_KEYS
_CANONICAL = {"failOpen": "fail_open", "pipelineMode": "pipeline_mode"}


def pipeline_settings(value):
    """Require tenant opt-in; never use Python truthiness on wire values.

    Historical camelCase names are accepted. Conflicting aliases are refused
    instead of making policy depend on which consumer reads the document.
    """
    flags = [
        _BOOLEAN.validate_python(value[key])
        for key in _FAIL_OPEN_KEYS
        if key in value and value[key] is not None
    ]
    if len(set(flags)) > 1:
        raise ValueError("conflicting fail_open aliases")
    modes = [
        str(value[key]).strip().lower()
        for key in _PIPELINE_MODE_KEYS
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


def saved_pipeline_settings(value):
    """Read settings from a saved version without refusing it.

    Versions saved before writes were checked can hold both spellings of a
    setting, or a value writes now refuse. Reading one must not fail, or the
    organization could not load, edit or push its settings. Fail Open counts
    only when every present spelling agrees it is on. The canonical
    pipeline_mode wins over its alias, and anything other than sequential runs
    in parallel, as the gateway does. Where pipeline_settings accepts a
    document, both functions return the same result.
    """
    try:
        flags = {
            _BOOLEAN.validate_python(value[key])
            for key in _FAIL_OPEN_KEYS
            if value.get(key) is not None
        }
    except ValueError:
        flags = set()
    mode = next(
        (value[key] for key in _PIPELINE_MODE_KEYS if value.get(key) is not None),
        "parallel",
    )
    return {
        "fail_open": flags == {True},
        "pipeline_mode": (
            "sequential" if str(mode).strip().lower() == "sequential" else "parallel"
        ),
    }


def canonical_setting_keys(patch):
    """Spell a settings patch with canonical keys so it replaces the saved value.

    A patch that carries both spellings keeps both, so the strict check still
    refuses them when they disagree.
    """
    result = dict(patch)
    for alias, canonical in _CANONICAL.items():
        if alias in result and canonical not in result:
            result[canonical] = result.pop(alias)
    return result


def _without_aliases(value):
    return {key: item for key, item in value.items() if key not in _ALIASES}


def normalize_guardrail_settings(value):
    """Preserve check definitions and return one canonical setting per policy."""
    return {**_without_aliases(value), **pipeline_settings(value)}


def normalize_saved_guardrail_settings(value):
    """Canonical settings for a saved version; never refuses the document."""
    return {**_without_aliases(value), **saved_pipeline_settings(value)}
