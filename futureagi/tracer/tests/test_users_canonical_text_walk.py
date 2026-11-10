"""Canonical picked strings keep exhaustive discovery and exact latest order keys."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_users_attribute_physical_replay import (
    PROJECT,
    REMAP,
    START,
    UNMAPPED,
    USER,
    execute,
    span,
)

pytestmark = pytest.mark.unit


def picked(value):
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [value],
            "attribute_value_types": ["string"],
        },
    }


def manager_and_builder(value):
    items = [picked(value)]
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        requested_columns=[],
        filters=items,
    )
    builder = UserListQueryBuilderV2(
        organization_id=PROJECT, project_ids=[PROJECT], filters=items
    )
    return manager, builder


@pytest.mark.parametrize("value", ['{"a":1,"b":2}', "[1,2]", "true", " FALSE "])
def test_canonical_picker_uses_bounded_presence_not_raw_equality(value):
    manager, builder = manager_and_builder(value)
    assert manager.attribute_exact_text_filters == {}
    assert manager.matching_activity_walk_applies(builder)
    witness = manager._walk_witness
    assert witness.kind == "canonical_text"
    assert not witness.index_pruned
    builder.walk_witness = witness
    sql, params = builder.build_matching_activity_slice_query(
        slice_start=START, slice_end=START + timedelta(minutes=1), limit=8
    )
    assert "mapContains(attrs_string, %(canonical_text_key)s)" in sql
    assert "lowerUTF8" not in sql
    assert "scalar_witness_identities" not in sql
    assert "exact_usage" not in sql
    assert params["canonical_text_key"] == "tag"
    assert params["slice_user_limit"] == 8


@pytest.mark.parametrize(
    "selected,equivalent,different",
    [
        ('{"a":1,"b":2}', ' { "b" : 2, "a" : 1 } ', '{"a":2,"b":2}'),
        ('{"a":{"x":1,"y":2}}', '{"a": {"y":2, "x":1}}', '{"a":{"x":2,"y":2}}'),
        ("[1,2]", " [ 1, 2 ] ", "[2,1]"),
        ("true", " \tTRUE\n", "false"),
        ('{"key":"value"}', '{"k\\u0065y":"VALUE"}', '{"key":"other"}'),
    ],
)
def test_canonical_picker_replays_equivalents_and_latest_matching_time(
    selected, equivalent, different
):
    manager, builder = manager_and_builder(selected)
    assert manager.matching_activity_walk_applies(builder)
    match_time = START + timedelta(minutes=1)
    newer = START + timedelta(minutes=2)
    rows = [
        span("equivalent", attrs_string={"tag": equivalent}, start_time=match_time),
        span("nonmatching-newer", attrs_string={"tag": different}, start_time=newer),
        span("wrong-user", end_user_id=UNMAPPED, attrs_string={"tag": different}),
        span("removed", attrs_string={"tag": selected}, start_time=newer),
        span("removed", attrs_string={}, start_time=newer, _version=2),
        span("deleted", attrs_string={"tag": selected}, start_time=newer),
        span(
            "deleted",
            attrs_string={"tag": selected},
            start_time=newer,
            _version=2,
            is_deleted=1,
        ),
        # String storage must not match a same-looking value stored as JSON.
        span(
            "wrong-storage",
            end_user_id=UNMAPPED,
            attributes_extra={"tag": {"a": 1, "b": 2}},
        ),
    ]

    def native_query(sql, params, **_kwargs):
        data = execute(rows, ["tag"], compiled=(sql, params))
        for row in data:
            if "canonical_string_activity" in row:
                row["canonical_string_activity"] = {
                    value: datetime.fromisoformat(moment).replace(tzinfo=UTC)
                    for value, moment in row["canonical_string_activity"].items()
                }
        return SimpleNamespace(data=data)

    users = [{"end_user_id": uid} for uid in (USER, UNMAPPED)]
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = native_query
        attributes = manager._read_span_attributes(
            users,
            None,
            start_date=START,
            end_date=START + timedelta(days=7),
            candidate_scan_ids=[*REMAP, UNMAPPED],
            candidate_end_user_id_map=REMAP,
        )
    manager._apply_span_attributes(users, attributes)
    assert [
        row["end_user_id"] for row in users if manager._row_matches_filters(row)
    ] == [USER]
    assert manager._matching_activity_by_user[USER]["tag"] == match_time.replace(
        tzinfo=UTC
    )
    assert manager._matching_activity_by_user[UNMAPPED] == {}


@pytest.mark.parametrize("value", ['{"a":1}', "true", "[1,2]"])
def test_typed_canonical_text_without_picker_keeps_existing_path(value):
    manager, builder = manager_and_builder(value)
    del manager.filters[0]["filter_config"]["attribute_value_types"]
    assert not manager.matching_activity_walk_applies(builder)
    assert manager.attribute_exact_text_filters == {}
