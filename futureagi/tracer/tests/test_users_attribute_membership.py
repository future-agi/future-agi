"""Users membership over typed, latest-per-span attribute values."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import serializers

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.unit

USER_ID = "00000000-0000-4000-8000-000000000001"
PROJECT_ID = "00000000-0000-4000-8000-000000000002"
ATTRIBUTE_KEY = "customer.tags"


@pytest.fixture
def manager() -> UsersListManager:
    return UsersListManager(
        organization_id="00000000-0000-4000-8000-000000000003",
        allowed_project_ids=[PROJECT_ID],
        requested_columns=[],
        attribute_keys=[ATTRIBUTE_KEY],
    )


def _config(filter_type, operation, expected):
    return FilterListField().run_validation(
        [
            {
                "column_id": ATTRIBUTE_KEY,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": filter_type,
                    "filter_op": operation,
                    "filter_value": expected,
                },
            }
        ]
    )[0]["filter_config"]


def _collected_row(manager, stored_values):
    """Exercise the collector's JSON serialization and storage provenance."""
    row = {"end_user_id": USER_ID}
    data = (
        [
            {
                "end_user_id": USER_ID,
                "attribute_key": ATTRIBUTE_KEY,
                "attribute_typed_values": [
                    (storage_type, json.dumps(value))
                    for storage_type, value in stored_values
                ],
            }
        ]
        if stored_values
        else []
    )
    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics:
        analytics.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=data
        )
        attributes = manager._read_span_attributes([row], ReadDeadline.start(10_000))
    manager._apply_span_attributes([row], attributes)
    return row


@pytest.mark.parametrize("operation", ["contains", "not_contains"])
@pytest.mark.parametrize(
    ("arrays", "expected", "contains"),
    [
        ([["vip", "active"]], ["missing", "vip"], True),
        ([["vip"], ["blocked"]], ["missing", "blocked"], True),
        ([["vip"], ["active"]], ["missing", "blocked"], False),
        ([["supervip"]], ["vip"], False),
        ([["VIP"]], ["vip"], False),
        ([["%_\\"]], ["%_\\"], True),
        ([["42"]], [42], False),
        ([[42]], ["42"], False),
        ([[True]], [1], False),
        ([[1]], [True], False),
        ([[False]], [0], False),
        ([[0]], [False], False),
        ([[True]], ["missing", True], True),
        ([[3.5]], [3.5], True),
        ([[1.0]], [1], True),
        # Match the compiler: safe integer selections include Double, while
        # explicitly floating selections only target Double source literals.
        ([[1]], [1.0], False),
        ([[(1 << 53) + 1]], [(1 << 53) + 1], True),
        ([[(1 << 53) + 1]], [1 << 53], False),
        ([[float(1 << 54)]], [1 << 54], False),
        ([[(1 << 64) - 1]], [(1 << 64) - 1], True),
        ([[-(1 << 63)]], [-(1 << 63)], True),
        ([[["vip"], {"tier": "vip"}, None]], ["vip"], False),
        ([[]], ["vip"], False),
    ],
)
def test_array_membership_uses_typed_members_across_spans(
    manager, arrays, expected, contains, operation
):
    row = _collected_row(manager, [("json", value) for value in arrays])

    assert manager._attribute_value_matches(
        row=row,
        key=ATTRIBUTE_KEY,
        config=_config("array", operation, expected),
    ) is (contains if operation == "contains" else not contains)


@pytest.mark.parametrize("operation", ["contains", "not_contains"])
@pytest.mark.parametrize(
    "stored_values",
    [
        [],
        [("json", None)],
        [("json", {"tier": "vip"})],
        [("json", "vip")],
        [("number", 42)],
        [("string", '["vip"]')],
        [("string", "[]")],
    ],
)
def test_array_membership_requires_an_actual_json_array(
    manager, stored_values, operation
):
    row = _collected_row(manager, stored_values)

    assert not manager._attribute_value_matches(
        row=row,
        key=ATTRIBUTE_KEY,
        config=_config("array", operation, ["vip"]),
    )


def test_non_array_values_do_not_supply_forbidden_array_members(manager):
    row = _collected_row(
        manager,
        [("json", []), ("string", '["blocked"]'), ("json", {"tag": "blocked"})],
    )

    assert manager._attribute_value_matches(
        row=row,
        key=ATTRIBUTE_KEY,
        config=_config("array", "not_contains", ["blocked"]),
    )


@pytest.mark.parametrize("filter_type", ["array", "list", "json"])
def test_array_filter_aliases_reach_canonical_membership(manager, filter_type):
    row = _collected_row(manager, [("json", ["vip", "active"])])
    config = _config(filter_type, "contains", ["missing", "vip"])

    assert config["filter_type"] == "array"
    assert manager._attribute_value_matches(row=row, key=ATTRIBUTE_KEY, config=config)


@pytest.mark.parametrize("operation", ["contains", "not_contains"])
@pytest.mark.parametrize("expected", [[], "vip", [None], [["vip"]], [float("inf")]])
def test_invalid_array_operands_fail_closed_for_internal_callers(
    manager, operation, expected
):
    row = _collected_row(manager, [("json", ["vip"])])

    assert not manager._attribute_value_matches(
        row=row,
        key=ATTRIBUTE_KEY,
        config={
            "filter_type": "array",
            "filter_op": operation,
            "filter_value": expected,
        },
    )


@pytest.mark.parametrize("filter_type", ["text", "string"])
@pytest.mark.parametrize("operation", ["contains", "not_contains"])
def test_text_substring_list_operand_is_rejected_by_serializer(filter_type, operation):
    with pytest.raises(serializers.ValidationError, match="must be strings"):
        _config(filter_type, operation, ["vip", "active"])


@pytest.mark.parametrize(
    ("operation", "expected", "matches"),
    [
        ("contains", "vip", True),
        ("contains", "%_", True),
        ("contains", "missing", False),
        ("not_contains", "vip", False),
        ("not_contains", "missing", True),
    ],
)
def test_text_scalar_substring_behavior_is_unchanged(
    manager, operation, expected, matches
):
    row = _collected_row(manager, [("string", "VIP%_customer"), ("string", "active")])

    assert (
        manager._attribute_value_matches(
            row=row,
            key=ATTRIBUTE_KEY,
            config=_config("text", operation, expected),
        )
        is matches
    )


@pytest.mark.parametrize("operation", ["contains", "not_contains"])
def test_map_member_filter_does_not_match_nested_member(manager, operation):
    row = _collected_row(manager, [("json", {"nested": {"attempt": 2}})])

    # The shared SQL compiler defines map containment over direct members,
    # not a substring anywhere in the serialized object.
    assert manager._attribute_value_matches(
        row=row,
        key=ATTRIBUTE_KEY,
        config=_config("map", operation, {"attempt": 2}),
    ) is (operation == "not_contains")
