"""Shared filter operator helpers.

Frontend panels have historically used display-oriented operator names such as
``equal_to`` and ``not_between``. Backend filter builders should use the
canonical API contract names below. The aliases remain only for saved filters
and older clients.
"""

from __future__ import annotations

from typing import Optional


FILTER_OP_ALIASES = {
    "is": "equals",
    "is_not": "not_equals",
    "equal_to": "equals",
    "not_equal_to": "not_equals",
    "not_between": "not_in_between",
    "inBetween": "between",
}


def normalize_filter_op(filter_op: Optional[str]) -> str:
    if not filter_op:
        return ""
    return FILTER_OP_ALIASES.get(filter_op, filter_op)
