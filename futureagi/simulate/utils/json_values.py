"""Comparing JSON values as JSON."""

from __future__ import annotations

import json


def same_json(one: object, other: object) -> bool:
    """Whether two values are the same JSON, nested values included: `true` is never `1`, nor `1.0` the integer `1`.
    Python's own equality takes `True == 1`."""
    return json.dumps(one, sort_keys=True, default=str) == json.dumps(
        other, sort_keys=True, default=str
    )
