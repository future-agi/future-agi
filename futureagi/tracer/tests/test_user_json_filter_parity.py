"""Offline Users matcher regressions against the typed predicate contract.

Expected outcomes are explicit synthetic examples, not SQL execution evidence.
The real serializer/collector and compiler are exercised without database reads.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.unit
UID = "00000000-0000-4000-8000-000000000001"
KEY = "customer.profile"


@pytest.fixture
def manager():
    return UsersListManager(
        organization_id="00000000-0000-4000-8000-000000000003",
        allowed_project_ids=["00000000-0000-4000-8000-000000000002"],
        requested_columns=[],
        attribute_keys=[KEY],
    )


def _wire(kind, operation, expected=None, **extra):
    return {
        "column_id": KEY,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": expected,
            **extra,
        },
    }


def _config(kind, operation, expected=None, **extra):
    return FilterListField().run_validation(
        [_wire(kind, operation, expected, **extra)]
    )[0]["filter_config"]


def _plan(config):
    return compile_trace_filter_plans([{"column_id": KEY, "filter_config": config}])[0]


def _collected_row(manager, stored):
    row = {"end_user_id": UID}
    data = (
        [
            {
                "end_user_id": UID,
                "attribute_key": KEY,
                "attribute_typed_values": [
                    (kind, json.dumps(value)) for kind, value in stored
                ],
            }
        ]
        if stored
        else []
    )
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=data)
        attrs = manager._read_span_attributes([row], ReadDeadline.start(10_000))
    manager._apply_span_attributes([row], attrs)
    return row


def _matches(manager, stored, config):
    return manager._attribute_value_matches(
        row=_collected_row(manager, stored), key=KEY, config=config
    )


@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "equals", "not_equals"]
)
@pytest.mark.parametrize(
    ("actual", "selected", "contains", "equals"),
    [
        ({"tier": "vip", "extra": 2}, {"tier": "vip"}, True, False),
        ({"tier": "supervip"}, {"tier": "vip"}, False, False),
        ({"tier": "VIP"}, {"tier": "vip"}, False, False),
        ({"nested": {"attempt": 2}}, {"attempt": 2}, False, False),
        ({"tier": "vip", "active": True}, {"active": True, "tier": "vip"}, True, True),
        ({"tier": "vip", "active": 1}, {"tier": "vip", "active": True}, False, False),
        ({"a.b": "%_"}, {"a.b": "%_"}, True, True),
        ({"a": {"b": "%_"}}, {"a.b": "%_"}, False, False),
        ({"n": "1"}, {"n": 1}, False, False),
        ({"n": 1}, {"n": "1"}, False, False),
        ({"n": True}, {"n": 1}, False, False),
        ({"n": 0}, {"n": False}, False, False),
        ({"n": 1.0}, {"n": 1}, True, True),
        ({"n": 1}, {"n": 1.0}, False, False),
        ({"n": float(1 << 54)}, {"n": 1 << 54}, False, False),
        ({"n": (1 << 53) + 1}, {"n": 1 << 53}, False, False),
        ({"n": (1 << 64) - 1}, {"n": (1 << 64) - 1}, True, True),
        ({"n": -(1 << 63)}, {"n": -(1 << 63)}, True, True),
        ({"n": None}, {"n": 0}, False, False),
        ({}, {"tier": "vip"}, False, False),
    ],
)
def test_map_direct_typed_members(
    manager, actual, selected, contains, equals, operation
):
    config = _config("json", operation, selected)
    assert config["filter_type"] == "map"
    plan = _plan(config)
    assert plan.predicate.startswith("latest_json_map_exists_0 AND")
    assert "JSONHas(latest_json_map_value_0" in plan.predicate
    assert ("JSONLength" in plan.predicate) == (operation in {"equals", "not_equals"})
    positive = equals if operation in {"equals", "not_equals"} else contains
    expected = not positive if operation.startswith("not_") else positive
    assert _matches(manager, [("json", actual)], config) is expected


@pytest.mark.parametrize("kind", ["map", "object", "json"])
def test_map_aliases_use_shared_value_sensitive_normalization(manager, kind):
    config = _config(kind, "contains", {"tier": "vip"})
    assert config["filter_type"] == "map"
    assert _matches(manager, [("json", {"tier": "vip", "extra": []})], config)
    # Internal callers also use the compiler's value-sensitive type alias.
    config["filter_type"] = kind
    assert _matches(manager, [("json", {"tier": "vip", "extra": []})], config)


@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "equals", "not_equals"]
)
@pytest.mark.parametrize(
    "stored",
    [
        [],
        [("json", None)],
        [("json", [])],
        [("json", "vip")],
        [("json", 42)],
        [("string", '{"tier":"vip"}')],
        [("number", 42)],
    ],
)
def test_map_requires_object_storage_even_for_negative(manager, stored, operation):
    config = _config("map", operation, {"tier": "vip"})
    assert "= 'Object'" in " ".join(_plan(config).aggregates)
    assert not _matches(manager, stored, config)


@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
@pytest.mark.parametrize(
    ("stored", "present"),
    [
        ([], False),
        ([("json", None)], False),
        ([("json", [])], False),
        ([("string", "{}")], False),
        ([("json", {})], True),
        ([("json", {"nested": []})], True),
    ],
)
def test_map_nullness_is_object_domain_presence(manager, stored, present, operation):
    config = _config("map", operation)
    plan = _plan(config)
    assert plan.exclude_group_matches is (operation == "is_null")
    assert _matches(manager, stored, config) is (
        not present if operation == "is_null" else present
    )


@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "equals", "not_equals"]
)
@pytest.mark.parametrize(
    "selected",
    [
        {},
        {"nested": {}},
        {"nested": []},
        {"n": None},
        {"n": float("inf")},
        {"n": float("nan")},
        {"n": 1 << 64},
        {"n": -(1 << 63) - 1},
        {"": "vip"},
        {"bad\nkey": "vip"},
        {"k" * 1025: "vip"},
        {"tier": "v" * 4097},
        {str(i): i for i in range(33)},
    ],
)
def test_invalid_flat_map_operands_fail_closed_like_compiler(
    manager, selected, operation
):
    config = _wire("map", operation, selected)["filter_config"]
    with pytest.raises(UnsupportedFilterShapeError):
        _plan(config)
    assert not _matches(manager, [("json", {"tier": "vip"})], config)


@pytest.mark.parametrize(
    ("kind", "operation", "expected", "wrong_domain"),
    [
        ("text", "not_equals", "blocked", ("number", 2)),
        ("text", "not_in", ["blocked"], ("boolean", False)),
        ("text", "not_contains", "blocked", ("json", {"tier": "other"})),
        ("number", "not_equals", 1, ("boolean", True)),
        ("number", "not_between", [1, 2], ("string", "3")),
        ("boolean", "not_equals", True, ("number", 0)),
    ],
)
@pytest.mark.parametrize("absent", [True, False])
def test_scalar_negatives_require_typed_presence(
    manager, kind, operation, expected, wrong_domain, absent
):
    config = _config(kind, operation, expected)
    assert _plan(config).predicate.startswith("latest_attr_exists_0 AND")
    assert not _matches(manager, [] if absent else [wrong_domain], config)


@pytest.mark.parametrize(
    ("stored", "matches"),
    [
        ([], False),
        ([("json", {})], False),
        ([("boolean", False)], False),
        ([("number", 2)], True),
        ([("number", 1)], False),
        ([("string", "other")], True),
        ([("string", "blocked")], False),
    ],
)
def test_mixed_negative_presence_uses_only_selected_domains(manager, stored, matches):
    config = _config(
        "text", "not_in", ["blocked", 1], attribute_value_types=["string", "number"]
    )
    assert _plan(config).predicate.startswith(
        "((latest_attr_exists_0_string OR latest_attr_exists_0_number) AND NOT"
    )
    assert _matches(manager, stored, config) is matches


@pytest.mark.parametrize("operation", ["contains", "equals"])
def test_map_requested_members_must_belong_to_one_object(manager, operation):
    config = _config("map", operation, {"tier": "vip", "active": True})
    assert not _matches(
        manager, [("json", {"tier": "vip"}), ("json", {"active": True})], config
    )
    assert _matches(
        manager, [("json", {"tier": "vip", "active": True}), ("json", {})], config
    )


@pytest.mark.parametrize("operation", ["not_contains", "not_equals"])
def test_map_retains_users_collection_negative_quantifier(manager, operation):
    config = _config("map", operation, {"tier": "vip"})
    assert not _matches(
        manager, [("json", {"tier": "vip"}), ("json", {"tier": "other"})], config
    )
    assert _matches(manager, [("json", {}), ("json", {"tier": "other"})], config)


@pytest.mark.parametrize("key", ["latency_ms", "user_id"])
@pytest.mark.parametrize(
    ("raw_attributes", "matches"),
    [(None, True), ({}, False), ({"VALUE": "raw"}, False), ({"VALUE": "native"}, True)],
)
def test_cache_presence_is_authoritative_even_when_attribute_absent(
    manager, key, raw_attributes, matches
):
    row = {"end_user_id": UID, key: "native"}
    if raw_attributes is not None:
        manager._attribute_values_by_user[UID] = (
            {key: raw_attributes["VALUE"]} if raw_attributes else {}
        )
    assert (
        manager._attribute_value_matches(
            row=row, key=key, config=_config("text", "equals", "native")
        )
        is matches
    )
