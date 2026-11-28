"""JSON values compared as JSON, never by Python's own equality."""

from __future__ import annotations

from decimal import Decimal

import pytest

from simulate.utils.json_values import same_json


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        (Decimal("1"), "1", False),
        (Decimal("1"), 1, True),
        (89, 89.0, True),
        (True, 1, False),
        ({"vip": True}, {"vip": 1}, False),
        ([1, {"a": None}], [1.0, {"a": None}], True),
        (None, 0, False),
        ("1", 1, False),
    ],
)
def test_two_values_are_the_same_json_only_by_json_s_own_rules(one, other, same):
    assert same_json(one, other) is same
