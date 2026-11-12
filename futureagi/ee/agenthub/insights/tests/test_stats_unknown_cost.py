"""A category's average cost leaves out items whose cost is unknown.

An item's cost is None when one of its model calls has no listed price, or
its spans couldn't be read; counting it as 0 made categories with unpriced
items look cheaper than the items with a cost (OBS-SW-145).
"""

import pytest

from ee.agenthub.insights.stats import describe, measure


def unit(key, cost, *, category="a", sub="a1"):
    return {
        "key": str(key),
        "unit_id": f"u{key}",
        "user_id": f"user{key}",
        "session_id": f"s{key}",
        "time": "2026-10-14T10:00:00",
        "facets": {"outcome": "resolved", "sentiment": 4, "attributes": {}},
        "metrics": {"latency_ms": 1000, "errors": 0, "turns": 3, "cost": cost},
        "category_id": category,
        "sub_category_id": sub,
    }


@pytest.mark.parametrize(
    ("costs", "expected"),
    [
        ([0.02, None], 0.02),
        ([0.02, None, 0.04, None], 0.03),
        ([0.0, None], 0.0),
        ([0.01, 0.03], 0.02),
    ],
    ids=["one-unknown", "half-unknown", "known-free", "all-known"],
)
def test_average_cost_reads_only_known_costs(costs, expected):
    units = [unit(i, cost) for i, cost in enumerate(costs)]
    assert describe(units, [])["cost"] == pytest.approx(expected)


@pytest.mark.parametrize("costs", [[None, None], []], ids=["all-unknown", "empty"])
def test_average_cost_is_unknown_without_a_known_cost(costs):
    units = [unit(i, cost) for i, cost in enumerate(costs)]
    assert describe(units, [])["cost"] is None


def test_category_and_report_totals_leave_unknown_costs_out():
    units = [
        unit(1, 0.02),
        unit(2, None),
        unit(3, 0.06, category="b", sub="b1"),
        unit(4, None, category="b", sub="b1"),
    ]
    tops = [{"id": "a", "level": 1}, {"id": "b", "level": 1}]
    subs = [
        {"id": "a1", "level": 2, "parent_id": "a"},
        {"id": "b1", "level": 2, "parent_id": "b"},
    ]
    result = measure(units, tops, subs, [])
    by_id = {c["id"]: c["stats"] for c in result["categories"]}
    assert by_id["a"]["cost"] == pytest.approx(0.02)
    assert by_id["b"]["cost"] == pytest.approx(0.06)
    assert result["totals"]["cost"] == pytest.approx(0.04)
