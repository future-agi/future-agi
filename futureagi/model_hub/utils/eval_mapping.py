"""Single definition of what a saved eval mapping value may be.

A mapping value is an attribute path string. Every resolver splits it on ".",
so any other type reaches a walker as an unhandled TypeError.

``None`` stays admissible: it is how the UI represents a cleared field, and
every resolver treats a falsy path as a missing attribute.

The mapping itself is not guaranteed to be a dict either. The OTEL eval-tag
path stored wire values verbatim after ``json.loads``, so a legacy row can hold
a list. ``.items()`` on one raises AttributeError, which lands in the broad
``except Exception`` these helpers exist to keep failures out of — so a non-dict
mapping is invalid here rather than a crash downstream.
"""

# Reported in place of a key when the mapping is not a dict at all: the whole
# value is the invalid thing, and there are no keys to name.
WHOLE_MAPPING_KEY = "<mapping>"


def _as_dict(mapping):
    """Return ``mapping`` as a dict, or ``None`` when it is not one."""
    if mapping is None:
        return {}
    return mapping if isinstance(mapping, dict) else None


def non_path_mapping_keys(mapping) -> list[str]:
    """Keys whose value is not an attribute path.

    Returns ``[WHOLE_MAPPING_KEY]`` when *mapping* is not a dict.
    """
    items = _as_dict(mapping)
    if items is None:
        return [WHOLE_MAPPING_KEY]
    return sorted(
        key
        for key, path in items.items()
        if path is not None and not isinstance(path, str)
    )


def require_mapping_paths(mapping, target: str) -> None:
    """Raise ValueError for any mapping value that is not an attribute path."""
    items = _as_dict(mapping)
    if items is None:
        raise ValueError(
            f"Mapping must be an object of attribute path strings, "
            f"got {type(mapping).__name__}, on {target}"
        )
    for key, attribute in items.items():
        if attribute is not None and not isinstance(attribute, str):
            raise ValueError(
                f"Mapping value for key '{key}' must be an attribute path string, "
                f"got {type(attribute).__name__}, on {target}"
            )
