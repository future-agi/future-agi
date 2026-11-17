"""Comparing JSON values as JSON."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


def same_json(one: object, other: object) -> bool:
    """Whether two values are the same JSON, nested values included: numbers by their value (`89` is `89.0`, a
    `Decimal("1")` is `1`), `true` never the number `1`, text never a number (`"1"` is not `1`), and `null` only
    `null`. Python's own equality takes `True == 1`."""
    if isinstance(one, bool) or isinstance(other, bool):
        return type(one) is type(other) and one == other
    if isinstance(one, Mapping) or isinstance(other, Mapping):
        return (
            isinstance(one, Mapping)
            and isinstance(other, Mapping)
            and one.keys() == other.keys()
            and all(same_json(one[key], other[key]) for key in one)
        )
    if isinstance(one, (list, tuple)) or isinstance(other, (list, tuple)):
        return (
            isinstance(one, (list, tuple))
            and isinstance(other, (list, tuple))
            and len(one) == len(other)
            and all(same_json(a, b) for a, b in zip(one, other, strict=True))
        )
    if isinstance(one, (int, float, Decimal)) and isinstance(
        other, (int, float, Decimal)
    ):
        return Decimal(str(one)) == Decimal(str(other))
    return type(one) is type(other) and one == other
