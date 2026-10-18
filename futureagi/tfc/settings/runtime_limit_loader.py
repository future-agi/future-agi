"""Build immutable runtime snapshots from shared numeric setting specs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import field, fields
from typing import Any, TypeVar

from tfc.settings.runtime_setting_specs import (
    Numeric,
    NumericSettingSpec,
    load_numeric_settings,
)

_SETTING_NAME = "setting_name"

T = TypeVar("T")


def runtime_setting(name: str, specs: Mapping[str, NumericSettingSpec]) -> Any:
    """Bind one dataclass field to a declared runtime setting."""

    if name not in specs:
        raise ValueError(f"{name} has no runtime setting specification")
    return field(metadata={_SETTING_NAME: name})


def load_setting_snapshot(
    model: type[T],
    *,
    specs: Mapping[str, NumericSettingSpec],
    source: object,
    fallback: object | None = None,
    validator: Callable[[Mapping[str, Numeric]], None] | None = None,
) -> T:
    """Resolve, validate, and freeze a complete settings group."""

    resolved = load_numeric_settings(specs, source=source, fallback=fallback)
    if validator is not None:
        validator(resolved)
    values: dict[str, Numeric] = {}
    for descriptor in fields(model):
        try:
            setting_name = descriptor.metadata[_SETTING_NAME]
        except KeyError as exc:
            raise TypeError(
                f"{descriptor.name} is missing runtime-setting metadata"
            ) from exc
        values[descriptor.name] = resolved[setting_name]
    return model(**values)
