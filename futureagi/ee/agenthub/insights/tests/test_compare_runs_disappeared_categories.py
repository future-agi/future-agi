"""Run changes must include categories pruned after their count reaches zero."""

import json
from copy import deepcopy

import pytest

from ee.agenthub.insights.stats import compare_runs


def _result(counts, *, total=100, level=1, **metrics):
    return {
        "totals": {"count": total, **metrics},
        "categories": [
            {"id": cid, "name": cid.upper(), "level": level, "stats": {"count": n}}
            for cid, n in counts.items()
        ],
    }


@pytest.mark.parametrize(
    ("level", "total"),
    [(1, 100), (2, 100), (1, 200), (2, 200)],
    ids=["top-equal", "sub-equal", "top-unequal", "sub-unequal"],
)
def test_disappeared_category_keeps_metadata_and_reports_zero_share(level, total):
    previous = _result({"a": 50, "b": 50}, level=level)
    current = _result({"b": total}, total=total, level=level)
    original = deepcopy((current, previous))

    changes = compare_runs(current, previous)

    rows = {row["category_id"]: row for row in changes["categories"]}
    assert set(rows) == {"a", "b"}
    vanished = rows["a"]
    assert vanished["name"] == "A"
    assert vanished["level"] == level
    assert vanished["count"] == 0
    assert vanished["previous_count"] == 50
    assert vanished["share"] == 0.0
    assert vanished["previous_share"] == 0.5
    assert vanished["delta"] == -0.5
    assert vanished["is_new"] is False
    assert vanished["significant"] is True
    assert 0 <= vanished["q"] < 0.1
    assert type(vanished["q"]) is float
    assert rows["b"]["count"] == total
    assert rows["b"]["share"] == 1.0
    assert rows["b"]["delta"] == 0.5
    assert changes["new_category_ids"] == []
    assert (current, previous) == original
    json.dumps(changes)


def test_union_keeps_current_metadata_and_only_flags_current_only_categories():
    previous = _result({"a": 50, "b": 50})
    current = _result({"b": 60, "c": 40})
    current["categories"][0]["name"] = "B renamed"

    changes = compare_runs(current, previous)

    rows = {row["category_id"]: row for row in changes["categories"]}
    assert len(changes["categories"]) == len(rows) == 3
    assert set(rows) == {"a", "b", "c"}
    assert rows["a"]["is_new"] is False
    assert rows["b"]["name"] == "B renamed"
    assert rows["b"]["level"] == 1
    assert rows["b"]["count"] == 60
    assert rows["b"]["previous_count"] == 50
    assert rows["b"]["is_new"] is False
    assert rows["c"]["is_new"] is True
    assert rows["c"]["previous_count"] == 0
    assert rows["c"]["previous_share"] == 0.0
    assert rows["c"]["delta"] == 0.4
    assert changes["new_category_ids"] == ["c"]
    assert changes["categories"][0]["category_id"] == "a"


def test_pruned_categories_share_joint_correction_with_headline_metrics():
    previous = _result(
        {"a": 20, "b": 80},
        failure_rate=0.30,
        unresolved_rate=0.25,
        error_rate=0.10,
    )
    current = _result(
        {"b": 60, "c": 40},
        failure_rate=0.20,
        unresolved_rate=0.15,
        error_rate=0.06,
    )
    # Before finish prunes zero counts, this same category already participates
    # in Fisher's exact tests and the joint category/headline BH correction.
    explicit_zero = deepcopy(current)
    explicit_zero["categories"].append(
        {"id": "a", "name": "A", "level": 1, "stats": {"count": 0}}
    )
    expected = compare_runs(explicit_zero, previous)

    changes = compare_runs(current, previous)

    assert {row["metric"] for row in changes["metrics"]} == {
        "failure_rate",
        "unresolved_rate",
        "error_rate",
    }
    assert changes["metrics"] == expected["metrics"]
    assert changes == expected
