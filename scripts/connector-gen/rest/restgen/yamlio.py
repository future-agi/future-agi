"""YAML and JSON reading for specs, overlays and provider records.

YAML is read as data only: no tags beyond the safe set, a repeated key is an
error (PyYAML would keep the last one silently), and timestamps stay
strings, so a date in a record reads back exactly as written."""

from __future__ import annotations

import json
from typing import Any

import yaml

_Base = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _Loader(_Base):  # type: ignore[misc, valid-type]
    pass


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in _Base.yaml_implicit_resolvers.items()
}


class DuplicateKeyError(ValueError):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    out: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in out:
            raise DuplicateKeyError(
                f"line {key_node.start_mark.line + 1}: key {key!r} is repeated"
            )
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(text: str) -> Any:
    """Parse one YAML document. Raises ValueError with a line on bad input."""
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 - a safe loader
    except DuplicateKeyError:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise ValueError(f"{line}not valid YAML ({problem})") from None


def load_document(text: str) -> Any:
    """Parse a spec: JSON when it starts with '{', else YAML."""
    if text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {exc.lineno}: not valid JSON ({exc.msg})") from None
    return load_yaml(text)


def has_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(has_null(v) for v in value.values())
    if isinstance(value, list):
        return any(has_null(v) for v in value)
    return False
