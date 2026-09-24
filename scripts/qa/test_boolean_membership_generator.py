"""Observed Boolean picker generation; synthetic engine fixtures, not customer results."""

from copy import deepcopy

import observe_span_reference as reference
import pytest
import replay_observe_filters as replay
import test_observe_span_reference as span_fixture
from rest_framework.exceptions import ValidationError
from test_observe_span_reference import case, public_ids, scope
from tracer.serializers.filters import FilterItemField

engine = span_fixture.engine


def attribute(values, family="SPAN_ATTRIBUTE"):
    return {
        "name": "key",
        "col_type": family,
        "resolved_type": "boolean",
        "seeds": [{"type": "boolean", "value": v} for v in values],
    }


def memberships(attr):
    return [
        (label, leaves, blocked)
        for label, leaves, blocked in replay.variants(attr)
        if label.startswith(("boolean:in:", "boolean:not_in:"))
    ]


@pytest.mark.parametrize(
    "values,expected,blocked_count",
    [
        ([], [], 4),
        ([False], [[False]], 2),
        ([True], [[True]], 2),
        ([False, True], [[False], [True], [False, True]], 0),
        ([True, False, True], [[True], [False], [True, False]], 0),
        ([0, 1, "false", None, False], [[False]], 2),
    ],
)
def test_only_observed_typed_values_and_explicit_missing_multiple(
    values, expected, blocked_count
):
    attr = attribute(values)
    original = deepcopy(attr)
    generated = memberships(attr)
    assert attr == original
    assert len([b for _, _, b in generated if b]) == blocked_count
    for op in ("in", "not_in"):
        ready = [
            leaves[0]
            for label, leaves, blocked in generated
            if label.startswith(f"boolean:{op}:") and not blocked
        ]
        assert [item["filter_config"]["filter_value"] for item in ready] == expected
        for item in ready:
            cfg = item["filter_config"]
            assert (
                cfg["filter_type"] == "text"
            )  # public picker envelope, not plain bool IN
            assert cfg["attribute_value_types"] == ["boolean"] * len(
                cfg["filter_value"]
            )
            assert all(type(v) is bool for v in cfg["filter_value"])
            assert FilterItemField().run_validation(deepcopy(item)) == item
    assert all(leaves == [] for _, leaves, blocked in generated if blocked)


@pytest.mark.parametrize(
    "family", ["SYSTEM_METRIC", "EVAL_METRIC", "ANNOTATION", "NORMAL"]
)
def test_picker_extension_does_not_broaden_other_families(family):
    assert memberships(attribute([False, True], family)) == []


def test_boolean_envelope_in_is_still_not_the_public_contract():
    item = replay.raw_leaf(attribute([False]), "boolean", "in", [False])
    with pytest.raises(ValidationError, match="Unsupported filter_op"):
        FilterItemField().run_validation(item)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("boolean:in:single:0", ["z-false"]),
        ("boolean:not_in:single:0", ["y-true"]),
        ("boolean:in:single:1", ["y-true"]),
        ("boolean:not_in:single:1", ["z-false"]),
        ("boolean:in:multiple", ["z-false", "y-true"]),
        ("boolean:not_in:multiple", []),
    ],
)
def test_generated_picker_on_existing_full_latest_rmt(engine, label, expected):
    generated = {
        name: leaves
        for name, leaves, blocked in memberships(attribute([False, True]))
        if not blocked
    }
    assert label in generated
    engine.insert("z-false", attrs_bool={"key": False}, attrs_number={"key": 1})
    engine.insert("y-true", attrs_bool={"key": True}, attrs_number={"key": 0})
    engine.insert("missing")
    engine.insert("number-only", attrs_number={"key": 0})
    engine.insert("string-only", attrs_string={"key": "false"})
    engine.insert("cleared", attrs_bool={"key": False})
    engine.insert("cleared", _version=2)
    engine.insert("deleted", attrs_bool={"key": True})
    engine.insert("deleted", attrs_bool={"key": True}, is_deleted=1, _version=2)
    normalized = [
        FilterItemField().run_validation(deepcopy(item)) for item in generated[label]
    ]
    result = reference.reference_span_ids(engine, case(), scope(), normalized)
    assert public_ids(result) == expected
    assert result[1]["population_exhausted"] is True
    assert result[1]["total_matches"] == (None if expected else 0)


def test_new_cases_leave_existing_equality_and_mixed_request_recipes_unchanged():
    attrs = [
        attribute([False, True]),
        {
            "name": "number",
            "resolved_type": "number",
            "seeds": [{"type": "number", "value": 2}],
        },
    ]
    plan = replay.make_plan(
        {
            "scope": {
                "organization_id": "org",
                "workspace_id": "ws",
                "project_id": "project",
            },
            "attributes": attrs,
        },
        replay.utc("2026-09-05T00:00:00Z"),
        ("traces",),
        1,
    )
    assert plan["version"] == 6
    assert (
        len(
            [
                c
                for c in plan["cases"]
                if c["variant"].startswith(("boolean:in:", "boolean:not_in:"))
            ]
        )
        == 18
    )
    for case_item in plan["cases"]:
        assert (
            case_item["id"]
            == replay.digest({k: v for k, v in case_item.items() if k != "id"})[:24]
        )
        if case_item["variant"].startswith("mixed:"):
            import json

            filters = json.loads(case_item["request"]["params"]["filters"])
            assert [f["filter_config"]["filter_op"] for f in filters[1:]] == [
                "equals",
                "equals",
            ]
