"""snake_case ↔ camelCase string converters.

Tiny, dependency-free helpers shared across apps. Lives in ``tfc.utils`` so
both the resolver (``tracer.utils.eval._walk_raw_log``) and any future
callers needing the same coercion can import without pulling Django models.
"""

from __future__ import annotations

import re

__all__ = ["to_camel_case", "to_snake_case"]


def to_camel_case(s: str) -> str:
    """``end_time`` → ``endTime``. No-op without underscores."""
    if "_" not in s:
        return s
    head, *tail = s.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in tail if p)


def to_snake_case(s: str) -> str:
    """``endTime`` → ``end_time``. No-op without uppercase."""
    if s == s.lower():
        return s
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()
